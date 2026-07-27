"""Cross-cutting HTTP middleware."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

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
        is_mutation = request.method in ("POST", "PUT", "PATCH", "DELETE")

        # A machine credential is bucketed per key, not per IP: the public API
        # (Phase 7.2) is authenticated by an API key, and treating it as
        # anonymous would both throttle it far too tightly and lump unrelated
        # keys behind one egress IP into a single counter. The key is not
        # validated here — like the JWT subject below, it only selects a
        # bucket, and a forged one still lands in *some* bucket while every
        # real authorization check happens later.
        credential = _api_key_credential(request)
        if credential is not None:
            counter = (
                rate_limit.PUBLIC_API_MUTATION
                if is_mutation
                else rate_limit.PUBLIC_API_GLOBAL
            )
            return f"apikey:{credential}", counter

        subject = _subject_from_access_token(request)

        if subject is None:
            forwarded = request.headers.get("x-forwarded-for")
            ip = (
                forwarded.split(",")[0].strip()
                if forwarded
                else (request.client.host if request.client else "unknown")
            )
            return f"ip:{ip}", rate_limit.ANONYMOUS_GLOBAL

        if is_mutation:
            return f"user:{subject}", rate_limit.AUTHENTICATED_MUTATION
        return f"user:{subject}", rate_limit.AUTHENTICATED_GLOBAL


#: Excluded from RED metrics: the scrape and the probes would otherwise inflate
#: the request rate with traffic that is not user work.
_METRICS_EXEMPT = frozenset({"/health", "/health/ready", "/health/live", "/metrics"})


class MetricsMiddleware:
    """RED metrics as a *pure ASGI* middleware, not a `BaseHTTPMiddleware`.

    The distinction matters: `BaseHTTPMiddleware` runs the app in an inner task
    and does not reliably surface the router's `scope["route"]` back to the
    outer handler, so the route *template* — the label that keeps per-id URLs
    from exploding cardinality — is lost. A pure ASGI middleware shares the one
    `scope` dict with the router, so the matched route is there after the app
    returns. It wraps `send` to capture the response status and times the whole
    exchange.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") in _METRICS_EXEMPT:
            await self.app(scope, receive, send)
            return

        from app.observability import metrics

        status_holder = {"code": 500}

        async def _send(message: Any) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
            await send(message)

        started = time.perf_counter()
        try:
            await self.app(scope, receive, _send)
        finally:
            elapsed = time.perf_counter() - started
            metrics.record_http(
                method=scope.get("method", "GET"),
                route=_route_template(scope),
                status_code=status_holder["code"],
                duration_seconds=elapsed,
                organization_id=_org_from_scope(scope),
            )


def _route_template(scope: Any) -> str:
    """The matched route as a template, so a per-id URL is one series.

    The router stores only each route's *local* path, so the full template is
    reconstructed from the request path with the matched path params folded back
    to their names (`/api/v1/deals/{id}` from `/api/v1/deals/abc-123`). An
    unmatched request (a 404 with no route) is bucketed as `unmatched` rather
    than minting a series per stray URL.
    """
    if scope.get("route") is None:
        return "unmatched"
    path = scope.get("path", "")
    for name, value in (scope.get("path_params") or {}).items():
        path = path.replace(str(value), "{" + name + "}")
    return path or "unmatched"


def _org_from_scope(scope: Any) -> str | None:
    """The unverified `org` claim, read from the ASGI scope's request.

    Same not-for-authorization read as the rate limiter: a forged claim only
    mislabels a metric, and every real check happens in the dependency chain.
    """
    return _org_from_access_token(Request(scope))


def _org_from_access_token(request: Request) -> str | None:
    """Read the `org` claim WITHOUT verifying the token — a metric label only.

    Same rationale as `_subject_from_access_token`: an attacker forging the claim
    mislabels a metric and gains nothing, and every real authorization check
    happens in the dependency chain.
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
        org = payload.get("org")
        return str(org) if org else None
    except Exception:
        return None


def _api_key_credential(request: Request) -> str | None:
    """Return a stable, non-reversible id for the request's API key, if any.

    Reads `X-API-Key` or a `vk_`-prefixed Bearer token — the same two carriers
    the machine-auth dependency accepts. The hash, never the raw key, becomes
    the rate-limit identifier, so the counter key and any log line built from it
    cannot leak the credential.
    """
    from app.core.security import API_KEY_PREFIX, hash_api_key

    raw = request.headers.get("x-api-key")
    if not raw:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            candidate = header[7:].strip()
            if candidate.startswith(API_KEY_PREFIX):
                raw = candidate
    if not raw:
        return None
    return hash_api_key(raw.strip())


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
