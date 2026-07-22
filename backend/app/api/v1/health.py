"""Liveness and readiness endpoints.

Split deliberately, because an orchestrator uses them differently:

- `/health`   liveness — is the process alive? Never touches a dependency. If
              this fails the container is restarted, so a slow database must
              not be able to trigger a restart loop.
- `/health/ready`  readiness — can it serve traffic? Checks dependencies. A
              failure removes the instance from the load balancer without
              killing it.

Conflating the two causes cascading restarts during a brief database blip.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.redis import check_redis
from app.db.session import check_database
from app.services.storage import get_object_storage

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    environment: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    checks: dict[str, bool]


@router.get("/health", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.PROJECT_NAME,
        environment=settings.ENVIRONMENT,
    )


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(response: Response) -> ReadinessResponse:
    # Storage is a readiness dependency, not a liveness one: an unreachable
    # bucket breaks uploads and downloads but leaves the rest of the CRM
    # working, so the instance should leave the load balancer rather than be
    # restarted into the same failure.
    checks = {
        "database": await check_database(),
        "redis": await check_redis(),
        "storage": await get_object_storage().verify_configuration(),
    }
    ready = all(checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ready else "degraded",
        checks=checks,
    )
