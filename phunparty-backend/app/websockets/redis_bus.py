from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import uuid
from collections import deque
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
REPLACEABLE_SESSION_MESSAGE_TYPES = {
    "beat_clock_state",
    "buzzer_state_update",
    "game_status_update",
    "roster_update",
    "session_stats",
    "sync_state",
    "ui_update",
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
        self.reliable_backlog_maxsize = int(
            os.getenv("WS_BUS_RELIABLE_BACKLOG_MAXSIZE", "5000")
        )
        self.reliable_backlog_session_max_bytes = int(
            os.getenv("WS_BUS_RELIABLE_BACKLOG_SESSION_MAX_BYTES", "8388608")
        )
        self.reliable_backlog_total_max_bytes = int(
            os.getenv("WS_BUS_RELIABLE_BACKLOG_TOTAL_MAX_BYTES", "33554432")
        )
        self._reliable_backlogs: dict[str, deque[tuple[dict[str, Any], int]]] = {}
        self._reliable_backlog_session_bytes: dict[str, int] = {}
        self._reliable_backlog_total_bytes = 0
        self.reliable_backlog_peak_bytes = 0
        self.dropped_event_count = 0
        self.dropped_control_event_count = 0
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

    def _ensure_dispatch_queue(
        self,
        session_code: str,
    ) -> asyncio.Queue[dict[str, Any]]:
        queue = self._dispatch_queues.get(session_code)
        if queue is None:
            queue = asyncio.Queue(maxsize=self.dispatch_queue_maxsize)
            self._dispatch_queues[session_code] = queue
            self._dispatch_tasks[session_code] = asyncio.create_task(
                self._dispatch_session_events(session_code),
                name=f"ws-redis-dispatch-{session_code}",
            )
        return queue

    def _event_coalesce_key(self, event: dict[str, Any]) -> tuple[Any, ...]:
        kind = event.get("kind")
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        message_type = message.get("type")

        if kind == "session_broadcast":
            return (
                kind,
                message_type,
                tuple(event.get("only_client_types") or []),
                tuple(event.get("exclude_client_types") or []),
            )
        if kind == "player_message":
            return (kind, event.get("player_id"), message_type)
        if kind == "disconnect_player":
            return (kind, event.get("player_id"))
        if kind == "revoke_connection_generation":
            return (kind, event.get("player_id"), event.get("generation"))
        return (kind,)

    def _event_is_replaceable(self, event: dict[str, Any]) -> bool:
        if self._is_control_event(event):
            return False
        if event.get("kind") != "session_broadcast":
            return False

        message = event.get("message")
        if not isinstance(message, dict):
            return False

        return message.get("type") in REPLACEABLE_SESSION_MESSAGE_TYPES

    def _replace_queued_event_with_same_key(
        self,
        queue: asyncio.Queue[dict[str, Any]],
        event: dict[str, Any],
    ) -> bool:
        key = self._event_coalesce_key(event)
        drained = []
        replaced = False

        while True:
            try:
                queued_event = queue.get_nowait()
                queue.task_done()
                drained.append(queued_event)
            except asyncio.QueueEmpty:
                break

        for queued_event in drained:
            if (
                not replaced
                and self._event_is_replaceable(queued_event)
                and self._event_coalesce_key(queued_event) == key
            ):
                replaced = True
                continue
            queue.put_nowait(queued_event)

        if replaced:
            queue.put_nowait(event)
        return replaced

    def _evict_replaceable_queued_event(
        self,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> bool:
        drained = []
        evicted = False

        while True:
            try:
                queued_event = queue.get_nowait()
                queue.task_done()
                drained.append(queued_event)
            except asyncio.QueueEmpty:
                break

        for queued_event in drained:
            if not evicted and self._event_is_replaceable(queued_event):
                evicted = True
                continue
            queue.put_nowait(queued_event)

        return evicted

    def _enqueue_reliable_backlog(
        self,
        session_code: str,
        event: dict[str, Any],
    ) -> bool:
        backlog = self._reliable_backlogs.setdefault(session_code, deque())
        event_bytes = len(json.dumps(event, separators=(",", ":")).encode("utf-8"))
        session_bytes = self._reliable_backlog_session_bytes.get(session_code, 0)
        if (
            len(backlog) >= self.reliable_backlog_maxsize
            or session_bytes + event_bytes > self.reliable_backlog_session_max_bytes
            or self._reliable_backlog_total_bytes + event_bytes
            > self.reliable_backlog_total_max_bytes
        ):
            if self._is_control_event(event):
                self.dropped_control_event_count += 1
            else:
                self.dropped_event_count += 1
            logger.error(
                "Redis WebSocket reliable backlog full; dropped newest event: session=%s kind=%s session_bytes=%s total_bytes=%s",
                session_code,
                event.get("kind"),
                session_bytes,
                self._reliable_backlog_total_bytes,
            )
            return False

        backlog.append((event, event_bytes))
        self._reliable_backlog_session_bytes[session_code] = (
            session_bytes + event_bytes
        )
        self._reliable_backlog_total_bytes += event_bytes
        self.reliable_backlog_peak_bytes = max(
            self.reliable_backlog_peak_bytes,
            self._reliable_backlog_total_bytes,
        )
        if self._is_control_event(event):
            self.control_backpressure_count += 1
        return True

    def _pop_reliable_backlog_event(
        self,
        session_code: str,
    ) -> Optional[dict[str, Any]]:
        backlog = self._reliable_backlogs.get(session_code)
        if not backlog:
            return None

        event, event_bytes = backlog.popleft()
        next_session_bytes = max(
            0,
            self._reliable_backlog_session_bytes.get(session_code, 0) - event_bytes,
        )
        if next_session_bytes:
            self._reliable_backlog_session_bytes[session_code] = next_session_bytes
        else:
            self._reliable_backlog_session_bytes.pop(session_code, None)
        self._reliable_backlog_total_bytes = max(
            0,
            self._reliable_backlog_total_bytes - event_bytes,
        )
        return event

    def _drain_reliable_backlog(self, session_code: str) -> None:
        backlog = self._reliable_backlogs.get(session_code)
        if not backlog:
            self._reliable_backlogs.pop(session_code, None)
            self._reliable_backlog_session_bytes.pop(session_code, None)
            return

        queue = self._ensure_dispatch_queue(session_code)
        while backlog and not queue.full():
            event = self._pop_reliable_backlog_event(session_code)
            if event is not None:
                queue.put_nowait(event)

        if not backlog:
            self._reliable_backlogs.pop(session_code, None)
            self._reliable_backlog_session_bytes.pop(session_code, None)

    async def _enqueue_event(self, event: dict[str, Any]) -> None:
        if not self._dispatcher:
            return

        session_code = event["session_code"]
        queue = self._ensure_dispatch_queue(session_code)

        if self._reliable_backlogs.get(session_code):
            self._drain_reliable_backlog(session_code)
            if self._reliable_backlogs.get(session_code):
                if self._event_is_replaceable(event):
                    self.dropped_event_count += 1
                    logger.warning(
                        "Dropped replaceable Redis WebSocket event behind reliable backlog: session=%s kind=%s",
                        session_code,
                        event.get("kind"),
                    )
                    return
                self._enqueue_reliable_backlog(session_code, event)
                return

        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            if self._event_is_replaceable(event):
                if self._replace_queued_event_with_same_key(queue, event):
                    self.dropped_event_count += 1
                    logger.debug(
                        "Coalesced replaceable Redis WebSocket event: session=%s kind=%s",
                        session_code,
                        event.get("kind"),
                    )
                    return

                self.dropped_event_count += 1
                logger.warning(
                    "Dropped replaceable Redis WebSocket event because dispatch queue is full: session=%s kind=%s",
                    session_code,
                    event.get("kind"),
                )
                return

            evicted_replaceable = self._evict_replaceable_queued_event(queue)
            if evicted_replaceable:
                self.dropped_event_count += 1
                queue.put_nowait(event)
                return

            self._enqueue_reliable_backlog(session_code, event)

    def _is_control_event(self, event: dict[str, Any]) -> bool:
        if event.get("kind") in CONTROL_EVENT_KINDS:
            return True
        return bool(event.get("critical") or event.get("require_ack"))

    async def _dispatch_session_events(self, session_code: str) -> None:
        queue = self._dispatch_queues[session_code]

        while True:
            try:
                self._drain_reliable_backlog(session_code)
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=self.dispatch_queue_idle_seconds,
                )
            except asyncio.TimeoutError:
                self._drain_reliable_backlog(session_code)
                if queue.empty() and not self._reliable_backlogs.get(session_code):
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
        self._reliable_backlogs.clear()
        self._reliable_backlog_session_bytes.clear()
        self._reliable_backlog_total_bytes = 0
        self._pubsub = None
        self._redis = None
        self._sync_redis = None


websocket_bus = RedisWebSocketBus()
