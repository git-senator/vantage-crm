"""Distributed rate limiting on Redis.

Sliding window over a sorted set. Each attempt is a member scored by timestamp;
counting is a range query and expiry is a range delete. Compared with the
alternatives:

  * **Fixed window** (`INCR` + `EXPIRE`) is cheaper but allows a 2x burst across
    a boundary — 5 attempts at 14:59:59 and 5 more at 15:00:00 pass a
    "5 per minute" limit.
  * **Token bucket** is smoother but needs two values and careful clock
    handling for no benefit at this scale.

The check-and-increment runs as a **Lua script**, so it is atomic. A read
followed by a separate write is a race: two requests can both observe "4 of 5
used" and both proceed. Under credential stuffing that race is the whole
attack.

Limits are layered, not merged. Login is limited per-IP *and* per-account
independently — a single IP hammering many accounts and a distributed botnet
hammering one account are different attacks, and one counter cannot see both.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

# Sliding-window check-and-increment, atomically.
#
# KEYS[1] window key
# ARGV[1] now (ms)   ARGV[2] window (ms)   ARGV[3] limit   ARGV[4] member id
#
# Returns {allowed, remaining, reset_after_ms}.
#
# The attempt is only recorded when it is allowed. Recording rejected attempts
# would let an attacker who is already being throttled extend their own
# lockout indefinitely — turning the limiter into a denial-of-service tool
# against the legitimate account holder.
_SLIDING_WINDOW_SCRIPT = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local used = redis.call('ZCARD', key)

if used >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local reset = window
    if oldest[2] then
        reset = (tonumber(oldest[2]) + window) - now
    end
    if reset < 0 then reset = 0 end
    return {0, 0, reset}
end

redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
return {1, limit - used - 1, window}
"""


@dataclass(frozen=True, slots=True)
class RateLimit:
    """A named limit: `limit` attempts per `window_seconds`."""

    name: str
    limit: int
    window_seconds: int

    @property
    def window_ms(self) -> int:
        return self.window_seconds * 1000


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    #: Seconds until the window frees up. Sent as Retry-After on a 429.
    reset_after_seconds: int
    limit: RateLimit


# ------------------------------------------------------------------ policies
#
# Login is the aggressive one: it is the endpoint an attacker actually targets.
# The per-account limit is deliberately tighter than per-IP, because a
# distributed attack spreads across IPs but converges on one account.

LOGIN_PER_IP = RateLimit("login_ip", limit=10, window_seconds=900)
LOGIN_PER_ACCOUNT = RateLimit("login_account", limit=5, window_seconds=900)
REFRESH_PER_TOKEN = RateLimit("refresh_token", limit=30, window_seconds=60)
PASSWORD_CHANGE = RateLimit("password_change", limit=5, window_seconds=3600)

#: Broad backstops, so an authenticated client cannot exhaust the database.
AUTHENTICATED_GLOBAL = RateLimit("authenticated", limit=1000, window_seconds=60)
AUTHENTICATED_MUTATION = RateLimit("mutation", limit=100, window_seconds=60)

#: Public API (Phase 7.2), bucketed per API key rather than per IP: a machine
#: integration is expected to be busier than a browser, and one key hammering
#: the API must not throttle another key that happens to share an egress IP.
PUBLIC_API_GLOBAL = RateLimit("public_api", limit=600, window_seconds=60)
PUBLIC_API_MUTATION = RateLimit("public_api_mutation", limit=120, window_seconds=60)

#: Unauthenticated traffic that is not login (health, static-ish endpoints).
ANONYMOUS_GLOBAL = RateLimit("anonymous", limit=60, window_seconds=60)


async def check(limit: RateLimit, identifier: str) -> RateLimitResult:
    """Consume one unit against `limit` for `identifier`.

    **Fails open.** If Redis is unavailable the request is allowed. A limiter
    that rejects traffic during a cache outage converts a degraded dependency
    into a full outage, and rate limiting is a mitigation rather than the
    primary control — account lockout and RBAC still apply.
    """
    key = f"ratelimit:{limit.name}:{identifier}"
    now_ms = int(time.time() * 1000)
    # Unique per attempt: a sorted-set member is a set element, so reusing a
    # value would silently overwrite rather than count.
    member = f"{now_ms}-{time.perf_counter_ns()}"

    try:
        raw = await cast(
            "Any",
            get_redis().eval(
                _SLIDING_WINDOW_SCRIPT,
                1,
                key,
                str(now_ms),
                str(limit.window_ms),
                str(limit.limit),
                member,
            ),
        )
    except Exception:
        logger.warning(
            "rate_limit_backend_unavailable", extra={"limit": limit.name}
        )
        return RateLimitResult(
            allowed=True, remaining=limit.limit, reset_after_seconds=0, limit=limit
        )

    allowed, remaining, reset_ms = int(raw[0]), int(raw[1]), int(raw[2])
    return RateLimitResult(
        allowed=bool(allowed),
        remaining=remaining,
        # Round up: reporting 0 seconds when 200ms remain invites an immediate
        # retry that fails again.
        reset_after_seconds=max(1, -(-reset_ms // 1000)) if not allowed else 0,
        limit=limit,
    )


async def reset(limit: RateLimit, identifier: str) -> None:
    """Clear a counter.

    Called after a successful login so a user who fumbled their password twice
    is not throttled for the next fifteen minutes.
    """
    try:
        await get_redis().delete(f"ratelimit:{limit.name}:{identifier}")
    except Exception:
        logger.warning("rate_limit_reset_failed", extra={"limit": limit.name})
