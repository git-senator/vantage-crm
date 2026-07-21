"""Distributed locking on Redis.

Used to serialise operations that must not run concurrently across API
instances — chiefly refresh-token rotation, where a stampede would otherwise
trip reuse detection and log a legitimate user out (risk R7).

Deliberately *not* a full Redlock implementation. Redlock targets correctness
across independent Redis masters under partition; we run a single Redis, and
the failure mode here is a rare duplicate rotation rather than data loss. The
grace window in `AuthService.refresh` is what makes that duplicate harmless, so
paying Redlock's complexity and latency would buy nothing.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import cast

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

# Compare-and-delete. Releasing without checking ownership would let a slow
# holder delete a lock that has already expired and been re-acquired by
# someone else.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


# --------------------------------------------------------- circuit breaker
#
# When Redis is unreachable, every `set()` pays the full connection timeout
# before failing. Under load that turns a cache outage into a latency outage:
# each request stalls, and for refresh specifically the delay can push a
# legitimate client past the rotation grace window and get it flagged as token
# theft. That is not hypothetical — it is exactly what an IPv6 resolution stall
# produced during development.
#
# After a failure we stop trying for a short cooldown and go straight to the
# unserialised path, which is already safe.
_BREAKER_COOLDOWN_SECONDS = 5.0
_breaker_open_until: float = 0.0


def _breaker_is_open(now: float) -> bool:
    return now < _breaker_open_until


def _trip_breaker(now: float) -> None:
    global _breaker_open_until
    _breaker_open_until = now + _BREAKER_COOLDOWN_SECONDS


def reset_breaker() -> None:
    """Clear the cooldown. For tests, and for an explicit health recovery."""
    global _breaker_open_until
    _breaker_open_until = 0.0


async def acquire(
    key: str,
    *,
    ttl_ms: int = 5_000,
    wait_ms: int = 3_000,
    poll_ms: int = 25,
) -> str | None:
    """Try to take a lock, waiting up to `wait_ms`.

    Returns an ownership token, or None if the lock is held elsewhere for the
    whole wait, or if Redis is unavailable.

    `ttl_ms` bounds how long a crashed holder can block others — it must exceed
    the worst-case duration of the guarded work, or a slow operation loses its
    lock mid-flight.
    """
    loop = asyncio.get_running_loop()
    if _breaker_is_open(loop.time()):
        return None

    token = secrets.token_urlsafe(16)
    redis = get_redis()
    deadline = loop.time() + wait_ms / 1000

    while True:
        try:
            if await redis.set(key, token, nx=True, px=ttl_ms):
                return token
        except Exception:
            # Fail OPEN. Refusing to proceed would log every user out during a
            # cache outage — far worse than the rare duplicate the grace window
            # already tolerates.
            _trip_breaker(loop.time())
            logger.warning("lock_backend_unavailable", extra={"key": key})
            return None

        if loop.time() >= deadline:
            return None
        await asyncio.sleep(poll_ms / 1000)


async def release(key: str, token: str) -> None:
    """Release a lock we own. Never raises."""
    try:
        # redis-py types eval() as returning str | Awaitable[str]; on the
        # async client it is always awaitable.
        await cast("Awaitable[object]", get_redis().eval(_RELEASE_SCRIPT, 1, key, token))
    except Exception:
        # The TTL will clear it. Logged, not raised — a failed release must not
        # mask the outcome of the work the lock was protecting.
        logger.warning("lock_release_failed", extra={"key": key})


@asynccontextmanager
async def guard(
    key: str, *, ttl_ms: int = 5_000, wait_ms: int = 3_000
) -> AsyncIterator[bool]:
    """Hold a lock for the duration of a block.

    Yields True if the lock was held, False if it could not be acquired (or
    Redis was down). Callers must handle False — the guarded work still needs
    to be safe when it runs unserialised, because this lock is an optimisation,
    not a correctness guarantee.

        async with guard(f"refresh:{family_id}") as locked:
            if not locked:
                logger.info("proceeding without the lock")
            ...
    """
    token = await acquire(key, ttl_ms=ttl_ms, wait_ms=wait_ms)
    try:
        yield token is not None
    finally:
        if token is not None:
            await release(key, token)
