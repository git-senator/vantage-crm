"""Cross-cutting HTTP middleware."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core import rate_limit
from app.core.logging import get_logger, request_id_var

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Assign every request a correlation ID and log its completion.

    An inbound `X-Request-ID` is honoured so a trace can span Next.js and the
    API, but it is length-capped and never echoed raw into logs unbounded —
    the header is client-controlled input.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        inbound = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = inbound[:64] if inbound else str(uuid.uuid4())

        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()

        # Every log emitted below must happen BEFORE the contextvar is reset,
        # or the correlation ID is missing from exactly the lines that need it.
        # Hence the reset lives in `finally` and the logging does not.
        try:
            response = await call_next(request)

            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response.headers[REQUEST_ID_HEADER] = request_id

            # Health checks are excluded: they run every few seconds and would
            # otherwise dominate log volume.
            if request.url.path not in ("/health", "/health/ready"):
                logger.info(
                    "request_completed",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                    },
                )
            return response

        except Exception:
            logger.exception(
                "request_failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        finally:
            request_id_var.reset(token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defence-in-depth headers on API responses.

    The browser-facing headers (CSP, HSTS) are set by Next.js, which is what
    the browser actually talks to. These are the subset that still matter for a
    JSON API — chiefly ensuring a response is never sniffed or framed if the
    API is ever reached directly.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        # The `Server` banner is NOT removed here: uvicorn appends it at the
        # transport layer, after middleware has run, so stripping it at this
        # point has no effect. It is suppressed with `--no-server-header` on the
        # uvicorn command line instead (see backend/Dockerfile).
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Broad backstop so no single caller can exhaust the database.

    Deliberately coarse. Targeted limits (login per-account, refresh per-token)
    live on the endpoints, because only they know the right identifier.

    Identity comes from the access-token subject when present, falling back to
    the client address. Parsing the JWT properly here would duplicate the auth
    dependency for no benefit — the claim is used only to bucket a counter, and
    a forged one still lands in *some* bucket.
    """

    #: Never limited: probes run every few seconds by design.
    EXEMPT_PATHS = frozenset({"/health", "/health/ready"})

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        identifier, limit = self._bucket(request)
        result = await rate_limit.check(limit, identifier)

        if not result.allowed:
            logger.warning(
                "rate_limited_global",
                extra={
                    "limit": limit.name,
                    "path": request.url.path,
                    "method": request.method,
                },
            )
            return JSONResponse(
                status_code=429,
                media_type="application/problem+json",
                headers={"Retry-After": str(result.reset_after_seconds)},
                content={
                    "type": "https://vantage.crm/problems/rate-limited",
                    "title": "Too many requests",
                    "status": 429,
                    "detail": "Too many requests. Please slow down.",
                    "instance": request.url.path,
                },
            )

        response = await call_next(request)
        # setdefault, not assignment: an endpoint that applied a tighter,
        # more specific limit has already reported it, and overwriting would
        # tell the client the global limit rejected them when it did not.
        response.headers.setdefault("X-RateLimit-Limit", str(limit.limit))
        response.headers.setdefault(
            "X-RateLimit-Remaining", str(max(result.remaining, 0))
        )
        return response

    @staticmethod
    def _bucket(request: Request) -> tuple[str, rate_limit.RateLimit]:
        """Pick the counter and the identity to count against."""
        subject = _subject_from_access_token(request)

        if subject is None:
            forwarded = request.headers.get("x-forwarded-for")
            ip = (
                forwarded.split(",")[0].strip()
                if forwarded
                else (request.client.host if request.client else "unknown")
            )
            return f"ip:{ip}", rate_limit.ANONYMOUS_GLOBAL

        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            return f"user:{subject}", rate_limit.AUTHENTICATED_MUTATION
        return f"user:{subject}", rate_limit.AUTHENTICATED_GLOBAL


def _subject_from_access_token(request: Request) -> str | None:
    """Read `sub` from the access token WITHOUT verifying it.

    Verification belongs to the auth dependency. Here the claim only selects a
    counter bucket, and an attacker forging one gains nothing: they still
    consume a bucket, and every real authorization check happens later.
    """
    token = request.cookies.get("vg_access")
    if not token:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()

    if not token:
        return None

    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        subject = payload.get("sub")
        return str(subject) if subject else None
    except Exception:
        return None
