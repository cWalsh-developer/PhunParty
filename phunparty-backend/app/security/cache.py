import json
import logging
import os
import time
import fnmatch
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def redis_required() -> bool:
    default = os.getenv("ENVIRONMENT", "development").lower() == "production"
    return env_flag("REQUIRE_REDIS_CACHE", default=default)


class JsonCache:
    def __init__(self) -> None:
        self.redis_url = os.getenv("REDIS_URL")
        self.require_redis = redis_required()
        self._redis = None
        self._memory: dict[str, tuple[str, float]] = {}

        if self.require_redis and not self.redis_url:
            raise RuntimeError("REDIS_URL is required when Redis cache is mandatory")

        if self.require_redis and not redis:
            raise RuntimeError(
                "The redis package is required when Redis cache is mandatory"
            )

        if self.redis_url and redis:
            try:
                self._redis = redis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                )
                self._redis.ping()
            except Exception as exc:
                if self.require_redis:
                    raise RuntimeError(
                        "Redis cache is mandatory but unavailable"
                    ) from exc
                logger.warning("Redis cache unavailable; using memory cache: %s", exc)
                self._redis = None

    def _handle_redis_failure(self, operation: str, exc: Exception) -> None:
        if self.require_redis:
            raise RuntimeError(f"Redis cache {operation} failed") from exc

        logger.warning("Redis cache %s failed; using memory cache: %s", operation, exc)
        self._redis = None

    def get(self, key: str) -> Optional[Any]:
        raw = None
        if self._redis:
            try:
                raw = self._redis.get(key)
            except Exception as exc:
                self._handle_redis_failure("get", exc)

        if raw is None:
            entry = self._memory.get(key)
            if not entry:
                return None
            raw, expires_at = entry
            if expires_at <= time.time():
                self._memory.pop(key, None)
                return None

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        raw = json.dumps(value, default=json_default, separators=(",", ":"))
        if self._redis:
            try:
                self._redis.setex(key, ttl_seconds, raw)
                return
            except Exception as exc:
                self._handle_redis_failure("set", exc)

        self._memory[key] = (raw, time.time() + ttl_seconds)

    def delete(self, *keys: str) -> None:
        keys = tuple(key for key in keys if key)
        if not keys:
            return

        if self._redis:
            try:
                self._redis.delete(*keys)
            except Exception as exc:
                self._handle_redis_failure("delete", exc)

        for key in keys:
            self._memory.pop(key, None)

    def delete_pattern(self, *patterns: str) -> None:
        patterns = tuple(pattern for pattern in patterns if pattern)
        if not patterns:
            return

        if self._redis:
            try:
                keys_to_delete = []
                for pattern in patterns:
                    keys_to_delete.extend(
                        list(self._redis.scan_iter(match=pattern, count=250))
                    )

                if keys_to_delete:
                    self._redis.delete(*keys_to_delete)
            except Exception as exc:
                self._handle_redis_failure("pattern delete", exc)

        for key in list(self._memory.keys()):
            if any(fnmatch.fnmatch(key, pattern) for pattern in patterns):
                self._memory.pop(key, None)

    def get_or_set(self, key: str, ttl_seconds: int, factory: Callable[[], Any]) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached

        value = factory()
        self.set(key, value, ttl_seconds)
        return value


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


cache = JsonCache()


def profile_cache_key(viewer_id: str, target_id: str) -> str:
    return f"profile:viewer_{viewer_id}:target_{target_id}"


def profile_stats_cache_key(viewer_id: str, target_id: str) -> str:
    return f"profile_stats:viewer_{viewer_id}:target_{target_id}"


def friends_cache_key(player_id: str) -> str:
    return f"friends:{player_id}"


def friends_presence_cache_key(player_id: str) -> str:
    return f"friends_presence:{player_id}"


def invalidate_social_cache(*player_ids: str) -> None:
    keys = []
    for player_id in player_ids:
        if player_id:
            keys.extend(
                [
                    friends_cache_key(player_id),
                    friends_presence_cache_key(player_id),
                ]
            )
    cache.delete(*keys)


def invalidate_profile_cache(player_id: str) -> None:
    if not player_id:
        return

    cache.delete_pattern(
        f"profile:viewer_*:target_{player_id}",
        f"profile_stats:viewer_*:target_{player_id}",
    )


def invalidate_relationship_cache(player_a_id: str, player_b_id: str) -> None:
    invalidate_social_cache(player_a_id, player_b_id)
    invalidate_profile_cache(player_a_id)
    invalidate_profile_cache(player_b_id)


def invalidate_friends_presence_cache(db, player_id: str) -> None:
    if not player_id:
        return

    from app.database.friend_crud import list_friends

    friend_ids = [friend.player_id for friend in list_friends(db, player_id)]
    cache.delete(
        *[
            friends_presence_cache_key(friend_id)
            for friend_id in friend_ids
            if friend_id
        ]
    )
