"""Shared Redis client.

Used for rate limiting, the refresh-rotation mutex (docs/SECURITY.md §2.4), the
role→permission cache, and the ARQ job queue. A single pooled client is created
lazily and disposed on shutdown.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        settings = get_settings()
        _client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
    return _client


async def check_redis() -> bool:
    """Readiness probe. Returns False rather than raising."""
    try:
        return bool(await get_redis().ping())
    except Exception:
        logger.exception("redis_healthcheck_failed")
        return False


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
