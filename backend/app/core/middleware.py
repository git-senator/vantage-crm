"""Cross-cutting HTTP middleware."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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
