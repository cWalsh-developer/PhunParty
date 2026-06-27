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


class JsonCache:
    def __init__(self) -> None:
        self.redis_url = os.getenv("REDIS_URL")
        self._redis = None
        self._memory: dict[str, tuple[str, float]] = {}

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
                logger.warning("Redis cache unavailable; using memory cache: %s", exc)
                self._redis = None

    def get(self, key: str) -> Optional[Any]:
        raw = None
        if self._redis:
            try:
                raw = self._redis.get(key)
            except Exception as exc:
                logger.warning("Redis cache get failed; using memory cache: %s", exc)
                self._redis = None

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
                logger.warning("Redis cache set failed; using memory cache: %s", exc)
                self._redis = None

        self._memory[key] = (raw, time.time() + ttl_seconds)

    def delete(self, *keys: str) -> None:
        keys = tuple(key for key in keys if key)
        if not keys:
            return

        if self._redis:
            try:
                self._redis.delete(*keys)
            except Exception as exc:
                logger.warning("Redis cache delete failed: %s", exc)
                self._redis = None

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
                logger.warning("Redis cache pattern delete failed: %s", exc)
                self._redis = None

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
