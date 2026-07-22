"""Aggregate router for API v1.

One aggregation point so `main.py` does not accumulate a list of imports as the
surface grows.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    activities,
    attachments,
    audit,
    auth,
    clients,
    dashboard,
    deals,
    leads,
    notes,
    organizations,
    pipelines,
    properties,
    roles,
    tasks,
    timeline,
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
api_router.include_router(
    activities.router, prefix="/activities", tags=["activities"]
)
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(notes.router, prefix="/notes", tags=["notes"])
api_router.include_router(
    attachments.router, prefix="/attachments", tags=["attachments"]
)
api_router.include_router(timeline.router, prefix="/timeline", tags=["timeline"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])


# Phase 3.1 replaced the attachment placeholders with real object storage;
# the endpoints live on the same /attachments router. See docs/DOCUMENTS.md.
