from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis
import redis as sync_redis

logger = logging.getLogger(__name__)

EventDispatcher = Callable[[dict[str, Any]], Awaitable[None]]


def websocket_redis_required() -> bool:
    configured = os.getenv("WS_REDIS_REQUIRED")
    if configured is not None:
        return configured.lower() == "true"

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
        self.redis_url = os.getenv("REDIS_URL")
        self.channel = os.getenv("WS_REDIS_CHANNEL", "phun:prod:ws:events")
        self.worker_id = uuid.uuid4().hex

        self._redis: redis.Redis | None = None
        self._sync_redis: sync_redis.Redis | None = None
        self._pubsub = None
        self._reader_task: asyncio.Task | None = None
        self._dispatcher: EventDispatcher | None = None

    @property
    def connected(self) -> bool:
        return self._redis is not None

    @property
    def sync_client(self) -> sync_redis.Redis | None:
        return self._sync_redis

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
        )
        self._sync_redis = sync_redis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True,
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

        envelope = {**event, "origin_worker_id": self.worker_id}
        await self._redis.publish(
            self.channel,
            json.dumps(envelope, separators=(",", ":")),
        )

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

                event = json.loads(message["data"])
                if event.get("origin_worker_id") == self.worker_id:
                    continue

                if self._dispatcher:
                    await self._dispatcher(event)

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

        if self._pubsub:
            await self._pubsub.unsubscribe(self.channel)
            await self._pubsub.aclose()

        if self._redis:
            await self._redis.aclose()
        if self._sync_redis:
            self._sync_redis.close()

        self._reader_task = None
        self._pubsub = None
        self._redis = None
        self._sync_redis = None


websocket_bus = RedisWebSocketBus()
