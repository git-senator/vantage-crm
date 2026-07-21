"""Rate-limit dependencies for individual endpoints.

Endpoint-level rather than middleware-level, because the identifier differs per
route: login limits by email *and* IP, refresh by token, mutations by user. A
single middleware cannot know which without parsing bodies it has no business
reading.

The broad per-user backstop lives in middleware; this module carries the
targeted limits.
"""

from __future__ import annotations

from fastapi import Request, Response

from app.core import rate_limit
from app.core.exceptions import RateLimitedError
from app.core.logging import get_logger
from app.core.rate_limit import RateLimit, RateLimitResult

logger = get_logger(__name__)


def client_ip(request: Request) -> str:
    """Best-effort client address.

    `X-Forwarded-For` is trusted only because uvicorn runs with
    `--proxy-headers` behind the Next.js BFF. It is used for rate limiting and
    forensics, never for authorization — it is trivially spoofable if anything
    is ever placed in front without stripping it.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _apply_headers(response: Response, result: RateLimitResult) -> None:
    response.headers["X-RateLimit-Limit"] = str(result.limit.limit)
    response.headers["X-RateLimit-Remaining"] = str(max(result.remaining, 0))
    if not result.allowed:
        response.headers["Retry-After"] = str(result.reset_after_seconds)


async def enforce(
    limit: RateLimit,
    identifier: str,
    *,
    response: Response | None = None,
) -> None:
    """Consume one unit, raising 429 when exhausted."""
    result = await rate_limit.check(limit, identifier)

    if response is not None:
        _apply_headers(response, result)

    if not result.allowed:
        logger.warning(
            "rate_limited",
            extra={"limit": limit.name, "reset_after": result.reset_after_seconds},
        )
        raise RateLimitedError(
            "Too many attempts. Please wait before trying again.",
            retry_after=result.reset_after_seconds,
            limit=limit.limit,
        )


async def enforce_login(request: Request, response: Response, email: str) -> None:
    """Two independent limits on the login endpoint.

    Per-IP catches one host hammering many accounts. Per-account catches a
    distributed botnet converging on one account. Neither counter can see the
    other's attack, so both are required.

    The account key is the raw email rather than a user id: the account may not
    exist, and rejecting unknown addresses more cheaply than known ones would
    reintroduce the user-enumeration oracle that login is careful to avoid.
    """
    await enforce(rate_limit.LOGIN_PER_IP, client_ip(request), response=response)
    await enforce(rate_limit.LOGIN_PER_ACCOUNT, email.lower(), response=response)


async def clear_login_limits(request: Request, email: str) -> None:
    """Drop login counters after a successful authentication.

    Without this, a user who mistypes their password twice carries those
    failures for the rest of the window even though they then signed in
    correctly.
    """
    await rate_limit.reset(rate_limit.LOGIN_PER_IP, client_ip(request))
    await rate_limit.reset(rate_limit.LOGIN_PER_ACCOUNT, email.lower())
