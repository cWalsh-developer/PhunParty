from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis
import redis as sync_redis

logger = logging.getLogger(__name__)

EventDispatcher = Callable[[dict[str, Any]], Awaitable[None]]
SESSION_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,128}$")
PLAYER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,128}$")
ALLOWED_EVENT_KINDS = {
    "session_broadcast",
    "player_message",
    "disconnect_player",
    "revoke_connection_generation",
}
CONTROL_EVENT_KINDS = {
    "disconnect_player",
    "revoke_connection_generation",
}
ALLOWED_WS_MESSAGE_TYPES = {
    "answer_rejected",
    "answer_submitted",
    "beat_clock_answer_result",
    "beat_clock_question",
    "beat_clock_started",
    "beat_clock_state",
    "buzzer_rejected",
    "buzzer_state_update",
    "buzzer_winner",
    "connection_established",
    "correct_answer",
    "countdown_started",
    "error",
    "fair_play_focus_grace_started",
    "fair_play_question_reset",
    "fair_play_settings_updated",
    "fair_play_status_update",
    "game_ended",
    "game_started",
    "game_status_update",
    "incorrect_answer",
    "initial_state",
    "intro_skipped",
    "intro_started",
    "kicked_from_session",
    "ping",
    "player_answered",
    "player_flagged",
    "player_joined",
    "player_kicked",
    "player_left",
    "pong",
    "preload_question",
    "question_failed",
    "question_started",
    "roster_update",
    "session_stats",
    "sync_state",
    "ui_update",
}


def redis_namespace() -> str:
    configured = os.getenv("REDIS_NAMESPACE") or os.getenv("REDIS_KEY_PREFIX")
    if configured:
        return configured.strip().rstrip(":")

    environment = os.getenv("ENVIRONMENT", "production").strip().lower()
    if environment in {"prod", "production"}:
        return "phun:prod"
    if environment in {"stage", "staging"}:
        return "phun:staging"
    if environment in {"test", "testing"}:
        return "phun:test"
    return "phun:development"


def websocket_redis_required() -> bool:
    configured = os.getenv("WS_REDIS_REQUIRED")
    if configured is not None:
        return configured.lower() == "true"

    if os.getenv("ENVIRONMENT", "").lower() == "production":
        return True

    worker_hints = " ".join(
        [
            os.getenv("WEB_CONCURRENCY", ""),
            os.getenv("GUNICORN_CMD_ARGS", ""),
        ]
    )
    return (
        "--workers 1" not in worker_hints
        and "--workers=1" not in worker_hints
        and "-w 1" not in worker_hints
        and worker_hints.strip() not in {"", "1"}
    )


class RedisWebSocketBus:
    def __init__(self) -> None:
        self.redis_url = os.getenv("WS_REDIS_URL") or os.getenv("REDIS_URL")
        self.namespace = redis_namespace()
        self.channel = os.getenv("WS_REDIS_CHANNEL") or self.key("ws", "events")
        self.worker_id = uuid.uuid4().hex
        self.max_event_bytes = int(os.getenv("WS_BUS_MAX_EVENT_BYTES", "65536"))
        self.socket_connect_timeout = float(
            os.getenv("WS_REDIS_CONNECT_TIMEOUT", "0.5")
        )
        self.socket_timeout = float(os.getenv("WS_REDIS_SOCKET_TIMEOUT", "0.5"))
        self.dispatch_queue_maxsize = int(os.getenv("WS_BUS_QUEUE_MAXSIZE", "1000"))
        self.dispatch_queue_put_timeout = float(
            os.getenv("WS_BUS_QUEUE_PUT_TIMEOUT", "0.05")
        )
        self.dispatch_queue_idle_seconds = float(
            os.getenv("WS_BUS_QUEUE_IDLE_SECONDS", "60")
        )

        self._redis: redis.Redis | None = None
        self._sync_redis: sync_redis.Redis | None = None
        self._pubsub = None
        self._reader_task: asyncio.Task | None = None
        self._dispatcher: EventDispatcher | None = None
        self._dispatch_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._dispatch_tasks: dict[str, asyncio.Task] = {}
        self._control_put_tasks: set[asyncio.Task] = set()
        self.dropped_event_count = 0
        self.control_backpressure_count = 0

    @property
    def connected(self) -> bool:
        return self._redis is not None

    @property
    def sync_client(self) -> sync_redis.Redis | None:
        return self._sync_redis

    @property
    def async_client(self) -> redis.Redis | None:
        return self._redis

    def key(self, *parts: str) -> str:
        cleaned = [str(part).strip(":") for part in parts if str(part)]
        return ":".join([self.namespace, *cleaned])

    def _validate_event(self, event: dict[str, Any]) -> bool:
        if not isinstance(event, dict):
            return False

        version = event.get("version", 1)
        if version != 1:
            logger.warning("Rejected Redis WebSocket event with unsupported version")
            return False

        kind = event.get("kind")
        if kind not in ALLOWED_EVENT_KINDS:
            logger.warning("Rejected Redis WebSocket event with invalid kind: %s", kind)
            return False

        session_code = event.get("session_code")
        if not isinstance(session_code, str) or not SESSION_CODE_PATTERN.fullmatch(
            session_code
        ):
            logger.warning("Rejected Redis WebSocket event with invalid session_code")
            return False

        if kind in {
            "player_message",
            "disconnect_player",
            "revoke_connection_generation",
        }:
            player_id = event.get("player_id")
            if not isinstance(player_id, str) or not PLAYER_ID_PATTERN.fullmatch(
                player_id
            ):
                logger.warning("Rejected Redis WebSocket event with invalid player_id")
                return False

        if kind in {"session_broadcast", "player_message"}:
            message = event.get("message")
            if not self._validate_websocket_message(message):
                logger.warning("Rejected Redis WebSocket event with invalid message")
                return False

        if kind == "disconnect_player":
            messages = event.get("messages", [])
            if not isinstance(messages, list) or len(messages) > 5:
                logger.warning("Rejected Redis WebSocket disconnect with bad messages")
                return False
            if not all(
                self._validate_websocket_message(message) for message in messages
            ):
                logger.warning("Rejected Redis WebSocket disconnect message shape")
                return False

        if kind == "revoke_connection_generation":
            generation = event.get("generation")
            if not isinstance(generation, str) or len(generation) > 256:
                logger.warning("Rejected Redis WebSocket revoke with bad generation")
                return False

        return True

    def _validate_websocket_message(self, message: Any) -> bool:
        if not isinstance(message, dict):
            return False

        message_type = message.get("type")
        if not isinstance(message_type, str):
            return False
        if len(message_type) > 80:
            return False
        if message_type not in ALLOWED_WS_MESSAGE_TYPES:
            logger.warning(
                "Rejected Redis WebSocket message with disallowed type: %s",
                message_type,
            )
            return False

        return True

    async def connect(self, dispatcher: EventDispatcher) -> None:
        self._dispatcher = dispatcher

        if not self.redis_url:
            if websocket_redis_required():
                raise RuntimeError(
                    "REDIS_URL is required for distributed WebSocket mode"
                )
            logger.warning(
                "Redis WebSocket bus disabled because REDIS_URL is unset; "
                "WebSocket broadcasts remain process-local."
            )
            return

        self._redis = redis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=self.socket_connect_timeout,
            socket_timeout=self.socket_timeout,
            health_check_interval=30,
        )
        self._sync_redis = sync_redis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=self.socket_connect_timeout,
            socket_timeout=self.socket_timeout,
            health_check_interval=30,
        )
        await self._redis.ping()

        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(self.channel)

        self._reader_task = asyncio.create_task(
            self._reader(),
            name=f"ws-redis-bus-{self.worker_id}",
        )

        logger.info(
            "Redis WebSocket bus connected worker=%s channel=%s",
            self.worker_id,
            self.channel,
        )

    async def publish(self, event: dict[str, Any]) -> None:
        if not self._redis:
            if websocket_redis_required():
                raise RuntimeError("Redis WebSocket bus is not connected")
            return

        envelope = {**event, "version": 1, "origin_worker_id": self.worker_id}
        if not self._validate_event(envelope):
            raise ValueError("Invalid Redis WebSocket event")
        encoded = json.dumps(envelope, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > self.max_event_bytes:
            raise ValueError("Redis WebSocket event is too large")
        await self._redis.publish(
            self.channel,
            encoded,
        )

    async def _enqueue_event(self, event: dict[str, Any]) -> None:
        if not self._dispatcher:
            return

        session_code = event["session_code"]
        queue = self._dispatch_queues.get(session_code)
        if queue is None:
            queue = asyncio.Queue(maxsize=self.dispatch_queue_maxsize)
            self._dispatch_queues[session_code] = queue
            self._dispatch_tasks[session_code] = asyncio.create_task(
                self._dispatch_session_events(session_code),
                name=f"ws-redis-dispatch-{session_code}",
            )

        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            if self._is_control_event(event):
                self.control_backpressure_count += 1
                logger.warning(
                    "Redis WebSocket control event queued behind full dispatch queue: session=%s kind=%s size=%s",
                    session_code,
                    event.get("kind"),
                    queue.qsize(),
                )
                task = asyncio.create_task(
                    self._put_control_event_when_ready(session_code, queue, event),
                    name=f"ws-redis-control-put-{session_code}",
                )
                self._control_put_tasks.add(task)
                task.add_done_callback(self._control_put_tasks.discard)
                return

            logger.warning(
                "Redis WebSocket dispatch queue full for session=%s size=%s",
                session_code,
                queue.qsize(),
            )
            try:
                await asyncio.wait_for(
                    queue.put(event),
                    timeout=self.dispatch_queue_put_timeout,
                )
            except asyncio.TimeoutError:
                self.dropped_event_count += 1
                logger.error(
                    "Dropped Redis WebSocket event after dispatch queue timeout: session=%s kind=%s",
                    session_code,
                    event.get("kind"),
                )

    async def _put_control_event_when_ready(
        self,
        session_code: str,
        queue: asyncio.Queue[dict[str, Any]],
        event: dict[str, Any],
    ) -> None:
        try:
            await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Failed to enqueue Redis WebSocket control event: session=%s kind=%s",
                session_code,
                event.get("kind"),
            )

    def _is_control_event(self, event: dict[str, Any]) -> bool:
        if event.get("kind") in CONTROL_EVENT_KINDS:
            return True
        return bool(event.get("critical") or event.get("require_ack"))

    async def _dispatch_session_events(self, session_code: str) -> None:
        queue = self._dispatch_queues[session_code]

        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=self.dispatch_queue_idle_seconds,
                )
            except asyncio.TimeoutError:
                if queue.empty():
                    self._dispatch_queues.pop(session_code, None)
                    self._dispatch_tasks.pop(session_code, None)
                    return
                continue

            try:
                if self._dispatcher:
                    await self._dispatcher(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Redis WebSocket event dispatch failed session=%s kind=%s",
                    session_code,
                    event.get("kind"),
                )
            finally:
                queue.task_done()

    async def _reader(self) -> None:
        if not self._pubsub:
            return

        while True:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message is None:
                    await asyncio.sleep(0.01)
                    continue

                raw_data = message["data"]
                if len(str(raw_data).encode("utf-8")) > self.max_event_bytes:
                    logger.warning("Rejected oversized Redis WebSocket event")
                    continue

                event = json.loads(raw_data)
                if event.get("origin_worker_id") == self.worker_id:
                    continue

                if not self._validate_event(event):
                    continue

                await self._enqueue_event(event)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Redis WebSocket subscriber failed worker=%s",
                    self.worker_id,
                )
                await asyncio.sleep(1)

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task

        for task in list(self._dispatch_tasks.values()):
            task.cancel()
        for task in list(self._dispatch_tasks.values()):
            with contextlib.suppress(asyncio.CancelledError):
                await task

        for task in list(self._control_put_tasks):
            task.cancel()
        for task in list(self._control_put_tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if self._pubsub:
            await self._pubsub.unsubscribe(self.channel)
            await self._pubsub.aclose()

        if self._redis:
            await self._redis.aclose()
        if self._sync_redis:
            self._sync_redis.close()

        self._reader_task = None
        self._dispatch_queues.clear()
        self._dispatch_tasks.clear()
        self._control_put_tasks.clear()
        self._pubsub = None
        self._redis = None
        self._sync_redis = None


websocket_bus = RedisWebSocketBus()
