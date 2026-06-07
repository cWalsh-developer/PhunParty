import hashlib
import logging
import os
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimit:
    scope: str
    limit: int
    window_seconds: int


class RateLimiter:
    def __init__(self) -> None:
        self.redis_url = os.getenv("REDIS_URL")
        self._redis = None
        self._memory: dict[str, tuple[int, float]] = {}

    async def connect(self) -> None:
        if self.redis_url and redis:
            self._redis = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            try:
                await self._redis.ping()
            except Exception as exc:
                logger.warning(
                    "Redis rate limiter unavailable; falling back to in-memory limits: %s",
                    exc,
                )
                await self._redis.close()
                self._redis = None

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()

    async def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        if self._redis:
            try:
                return await self._hit_redis(key, limit, window_seconds)
            except Exception as exc:
                logger.warning(
                    "Redis rate limiter failed; falling back to in-memory limits: %s",
                    exc,
                )
                try:
                    await self._redis.close()
                finally:
                    self._redis = None

        now = time.time()
        count, reset_at = self._memory.get(key, (0, now + window_seconds))

        if now > reset_at:
            count = 0
            reset_at = now + window_seconds

        count += 1
        self._memory[key] = (count, reset_at)

        retry_after = max(int(reset_at - now), 1)
        return count <= limit, retry_after

    async def _hit_redis(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        current = await self._redis.incr(key)

        if current == 1:
            await self._redis.expire(key, window_seconds)

        ttl = await self._redis.ttl(key)
        retry_after = ttl if ttl > 0 else window_seconds
        return current <= limit, retry_after


rate_limiter = RateLimiter()


def get_client_ip(request: Request) -> str:
    trust_proxy = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"

    if trust_proxy:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

    if request.client:
        return request.client.host

    return "unknown"


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


async def enforce_rate_limit(
    request: Request,
    *,
    scope: str,
    identifier: str,
    limit: int,
    window_seconds: int,
) -> None:
    key = f"rl:{scope}:{stable_hash(identifier)}"
    allowed, retry_after = await rate_limiter.hit(key, limit, window_seconds)

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def rate_limit_by_ip(scope: str, limit: int, window_seconds: int):
    async def dependency(request: Request):
        await enforce_rate_limit(
            request,
            scope=scope,
            identifier=get_client_ip(request),
            limit=limit,
            window_seconds=window_seconds,
        )

    return dependency
