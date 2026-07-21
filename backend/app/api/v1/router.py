"""Aggregate router for API v1.

One aggregation point so `main.py` does not accumulate a list of imports as the
surface grows.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    audit,
    auth,
    clients,
    deals,
    leads,
    organizations,
    pipelines,
    properties,
    roles,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(
    organizations.router, prefix="/organizations", tags=["organizations"]
)
api_router.include_router(roles.router, prefix="/roles", tags=["roles"])
api_router.include_router(audit.router, prefix="/audit-logs", tags=["audit"])
api_router.include_router(leads.router, prefix="/leads", tags=["leads"])
api_router.include_router(clients.router, prefix="/clients", tags=["clients"])
api_router.include_router(
    properties.router, prefix="/properties", tags=["properties"]
)
api_router.include_router(pipelines.router, prefix="/pipelines", tags=["pipelines"])
api_router.include_router(deals.router, prefix="/deals", tags=["deals"])


# Phase 2:    activities, tasks
