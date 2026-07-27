"""FastAPI application factory.

Note what is *absent*: CORS is not configured for production. The browser never
talks to this service — Next.js proxies to it over the internal network
(docs/ARCHITECTURE.md §2). Adding permissive CORS here would undo that
containment, so `CORS_ORIGINS` is asserted empty in production by
`Settings.assert_production_ready`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    CorrelationIdMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.redis import close_redis
from app.db.session import dispose_engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, json_output=settings.use_json_logs)

    # Refuse to start with unsafe production settings rather than discovering
    # the problem from traffic.
    settings.assert_production_ready()

    logger.info(
        "application_starting",
        extra={"environment": settings.ENVIRONMENT, "version": app.version},
    )
    yield
    logger.info("application_stopping")
    await dispose_engine()
    await close_redis()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        lifespan=lifespan,
        # Interactive docs are a reconnaissance aid; off in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # Middleware executes in reverse registration order, so the correlation ID
    # is registered last and therefore runs first — every downstream log line,
    # including error handlers, carries the request ID.
    app.add_middleware(SecurityHeadersMiddleware)

    # Registered before the correlation middleware so it runs *after* it —
    # Starlette executes middleware in reverse registration order. A rejected
    # request therefore still gets a request id in its logs.
    app.add_middleware(RateLimitMiddleware)

    if settings.CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
        )

    if settings.is_production:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["api", "localhost"])

    app.add_middleware(CorrelationIdMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    # The public, machine-facing API (Phase 7.2). A mounted sub-application so
    # it carries its own OpenAPI document and idempotency middleware; the
    # parent's correlation, rate-limit and security-header middleware still
    # wrap it. Authenticated by API keys only — no cookie, no CSRF.
    from app.api.public.app import create_public_app

    app.mount("/api/public/v1", create_public_app())

    return app


app = create_app()
