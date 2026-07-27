"""Liveness, readiness, and the metrics scrape.

Split deliberately, because an orchestrator uses them differently:

- `/health` / `/health/live`  liveness — is the process alive? Never touches a
              dependency. If this fails the container is restarted, so a slow
              database must not be able to trigger a restart loop.
- `/health/ready`  readiness — can it serve traffic? Checks dependencies, and
              reports each one's status and latency so a degraded probe says
              *what* is degraded. A failure removes the instance from the load
              balancer without killing it.
- `/metrics`  the Prometheus scrape — the RED signals and per-tenant usage the
              rest of the observability layer records. Optionally token-gated.

Conflating liveness and readiness causes cascading restarts during a brief
database blip, which is why the two are kept apart.
"""

from __future__ import annotations

import time
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Response, status
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.redis import check_redis
from app.core.security import constant_time_compare
from app.db.session import check_database
from app.observability import metrics
from app.services.storage import get_object_storage

router = APIRouter(tags=["health"])

#: Kept in step with the FastAPI app version in `app/main.py`.
VERSION = "0.1.0"

#: Process start, for an uptime a probe can read without a clock skew argument.
_STARTED_MONOTONIC = time.monotonic()


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    environment: str
    version: str
    uptime_seconds: float


class ComponentCheck(BaseModel):
    ok: bool
    latency_ms: float


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    checks: dict[str, ComponentCheck]


async def _timed(check) -> ComponentCheck:  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    try:
        ok = bool(await check())
    except Exception:
        ok = False
    return ComponentCheck(
        ok=ok, latency_ms=round((time.perf_counter() - started) * 1000, 2)
    )


def _liveness() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.PROJECT_NAME,
        environment=settings.ENVIRONMENT,
        version=VERSION,
        uptime_seconds=round(time.monotonic() - _STARTED_MONOTONIC, 2),
    )


@router.get("/health", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return _liveness()


@router.get("/health/live", response_model=HealthResponse)
async def liveness_alias() -> HealthResponse:
    """Alias for orchestrators that expect the Kubernetes-style `/live` path."""
    return _liveness()


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(response: Response) -> ReadinessResponse:
    # Storage is a readiness dependency, not a liveness one: an unreachable
    # bucket breaks uploads and downloads but leaves the rest of the CRM
    # working, so the instance should leave the load balancer rather than be
    # restarted into the same failure.
    checks = {
        "database": await _timed(check_database),
        "redis": await _timed(check_redis),
        "storage": await _timed(get_object_storage().verify_configuration),
    }
    ready = all(component.ok for component in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ready else "degraded",
        checks=checks,
    )


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """Prometheus text exposition of the RED and usage metrics.

    Open by default — the norm for a scrape on an internal network — unless
    `METRICS_TOKEN` is set, in which case a matching Bearer token is required.
    Returns 404 when metrics are disabled, so the path does not advertise a
    disabled feature.
    """
    settings = get_settings()
    if not settings.METRICS_ENABLED:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    token = settings.METRICS_TOKEN.get_secret_value()
    if token:
        presented = (
            authorization[7:].strip()
            if authorization and authorization.lower().startswith("bearer ")
            else ""
        )
        if not constant_time_compare(presented, token):
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    return Response(content=metrics.REGISTRY.render(), media_type="text/plain; version=0.0.4")
