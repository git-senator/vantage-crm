"""Cache-stampede protection (Phase 8.1).

A read-through cache where, on a miss, exactly one caller recomputes the value
while the rest wait for it — rather than a thundering herd all recomputing the
same expensive result the instant a hot key expires. It reuses the two
primitives this codebase already has: the Redis client for storage and the
distributed lock (`app.core.locks`) for single-flight.

Two touches make it correct rather than merely present:

  * **Double-checked after the lock.** The winner of the lock re-reads the cache
    before computing, so a value another caller just populated is used instead of
    recomputed.
  * **Jittered TTL.** Every entry expires at its base TTL plus a random spread,
    so a batch of keys written together does not all expire in the same instant
    and re-stampede — the herd is smeared across a window instead of synchronised.

It fails **open**: if Redis or the lock is unavailable the value is simply
computed and returned. A cache is an optimisation, never a correctness
dependency, so an outage degrades latency, not availability.
"""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast

from app.core.logging import get_logger

logger = get_logger(__name__)

#: A recompute that takes longer than this risks losing its lock mid-flight, so
#: the lock TTL is sized above any legitimate cached computation.
_LOCK_TTL_MS = 10_000
_LOCK_WAIT_MS = 5_000


def jittered_ttl(base_seconds: float, *, jitter_ratio: float = 0.2) -> float:
    """`base` plus up to `jitter_ratio` of it, so co-written keys do not expire
    together. Always at least the base, never more than `base * (1 + jitter)`."""
    if base_seconds <= 0:
        return base_seconds
    spread = base_seconds * max(0.0, jitter_ratio)
    return base_seconds + random.random() * spread  # noqa: S311 — cache spread, not crypto


class _RedisLike(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: str, *, ex: int | None = None) -> Any: ...


Locker = Callable[[str], "AbstractLock"]


class AbstractLock(Protocol):
    async def __aenter__(self) -> bool: ...
    async def __aexit__(self, *exc: object) -> None: ...


@asynccontextmanager
async def _default_locker(key: str) -> AsyncIterator[bool]:
    from app.core.locks import guard

    async with guard(key, ttl_ms=_LOCK_TTL_MS, wait_ms=_LOCK_WAIT_MS) as locked:
        yield locked


class StampedeCache:
    """Read-through cache with single-flight recompute.

    `redis` and `locker` are injectable so the stampede behaviour can be tested
    deterministically against in-memory doubles; both default to the real shared
    client and the Redis lock.
    """

    def __init__(
        self,
        *,
        namespace: str = "cache",
        default_ttl: float = 60.0,
        jitter_ratio: float = 0.2,
        redis: _RedisLike | None = None,
        locker: Locker | None = None,
    ) -> None:
        self.namespace = namespace
        self.default_ttl = default_ttl
        self.jitter_ratio = jitter_ratio
        self._redis = redis
        self._locker: Locker = locker or cast("Locker", _default_locker)

    def _resolve_redis(self) -> _RedisLike:
        if self._redis is not None:
            return self._redis
        from app.core.redis import get_redis

        # The shared client satisfies the read/write subset this cache uses; its
        # full signature is broader than the protocol, so the cast is exact.
        return cast("_RedisLike", get_redis())

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    async def get_or_set(
        self,
        key: str,
        compute: Callable[[], Awaitable[Any]],
        *,
        ttl: float | None = None,
    ) -> Any:
        """Return the cached value for `key`, computing it once on a miss.

        `compute` is an async callable so the caller controls what "recompute"
        means; its result must be JSON-serialisable, matching how the RBAC and
        rate-limit caches already store structured values.
        """
        full = self._key(key)
        redis = self._resolve_redis()

        hit = await self._read(redis, full)
        if hit is not _MISS:
            return hit

        async with self._locker(f"lock:{full}") as locked:
            if locked:
                # Double-check: another caller may have populated it while we
                # waited for the lock.
                hit = await self._read(redis, full)
                if hit is not _MISS:
                    return hit
            value = await compute()
            await self._write(redis, full, value, ttl)
            return value

    async def invalidate(self, key: str) -> None:
        redis = self._resolve_redis()
        try:
            await redis.set(self._key(key), _TOMBSTONE, ex=1)
        except Exception:
            logger.warning("cache_invalidate_failed", extra={"key": key})

    async def _read(self, redis: _RedisLike, full: str) -> Any:
        try:
            raw = await redis.get(full)
        except Exception:
            logger.warning("cache_read_failed", extra={"key": full})
            return _MISS
        if raw is None or raw == _TOMBSTONE:
            return _MISS
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return _MISS

    async def _write(
        self, redis: _RedisLike, full: str, value: Any, ttl: float | None
    ) -> None:
        seconds = int(jittered_ttl(ttl or self.default_ttl, jitter_ratio=self.jitter_ratio))
        try:
            await redis.set(full, json.dumps(value), ex=max(1, seconds))
        except Exception:
            logger.warning("cache_write_failed", extra={"key": full})


_MISS = object()
_TOMBSTONE = "\x00__invalidated__"


__all__ = ["StampedeCache", "jittered_ttl"]
