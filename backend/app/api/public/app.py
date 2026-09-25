"""The public API sub-application (Phase 7.2).

Mounted by the main app at `/api/public/v1`. Built as its own `FastAPI`
instance for two reasons:

  * **A self-contained OpenAPI document.** The public contract is described at
    `/api/public/v1/openapi.json`, independent of the internal API's schema, so
    a third party generating a client sees only the stable public surface.
  * **Its own idempotency middleware and problem-details handlers.** Writes on
    this surface honour `Idempotency-Key`; every error is RFC 9457
    `application/problem+json`, the same handlers the internal API uses.

The parent app's cross-cutting middleware (correlation id, the API-key-aware
rate limiter, security headers) still wraps requests to a mounted sub-app —
Starlette runs it before dispatching into the mount — so this does not
re-declare them. Authentication is API-key only; there is no login, cookie or
CSRF on this surface.

Docs stay reachable regardless of environment: a public API's schema *is* the
product, unlike the internal API whose interactive docs are a recon aid and are
disabled in production.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.public.v1.idempotency import IdempotencyMiddleware
from app.api.public.v1.router import public_v1_router
from app.core.exceptions import register_exception_handlers

PUBLIC_API_DESCRIPTION = (
    "The ROSSA CRM public API. Authenticate every request with an API key "
    "(Phase 7.1) via the `X-API-Key` header or a `Bearer` token. Responses are "
    "cursor-paginated; errors follow RFC 9457 problem+json. Send an "
    "`Idempotency-Key` header on writes to make retries safe."
)


def create_public_app() -> FastAPI:
    public_app = FastAPI(
        title="ROSSA CRM Public API",
        version="1.0.0",
        description=PUBLIC_API_DESCRIPTION,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    public_app.add_middleware(IdempotencyMiddleware)
    register_exception_handlers(public_app)
    public_app.include_router(public_v1_router)

    return public_app
