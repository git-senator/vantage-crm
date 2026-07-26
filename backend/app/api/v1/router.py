"""Aggregate router for API v1.

One aggregation point so `main.py` does not accumulate a list of imports as the
surface grows.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    activities,
    admin,
    ai,
    ai_assistant,
    ai_deals,
    ai_leads,
    analytics,
    attachments,
    audit,
    auth,
    automations,
    calendar,
    clients,
    conversations,
    dashboard,
    deals,
    jobs,
    leads,
    notes,
    notifications,
    organizations,
    pipelines,
    properties,
    reports,
    roles,
    tasks,
    timeline,
    whatsapp,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(
    organizations.router, prefix="/organizations", tags=["organizations"]
)
api_router.include_router(roles.router, prefix="/roles", tags=["roles"])
api_router.include_router(audit.router, prefix="/audit-logs", tags=["audit"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(leads.router, prefix="/leads", tags=["leads"])
api_router.include_router(
    automations.router, prefix="/automations", tags=["automations"]
)
api_router.include_router(
    calendar.router, prefix="/calendar", tags=["calendar"]
)
api_router.include_router(clients.router, prefix="/clients", tags=["clients"])
api_router.include_router(
    conversations.router, prefix="/conversations", tags=["conversations"]
)
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
    notifications.router, prefix="/notifications", tags=["notifications"]
)
api_router.include_router(
    attachments.router, prefix="/attachments", tags=["attachments"]
)
api_router.include_router(timeline.router, prefix="/timeline", tags=["timeline"])
api_router.include_router(
    whatsapp.router, prefix="/whatsapp", tags=["whatsapp"]
)
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(ai_assistant.router, prefix="/ai", tags=["ai-assistant"])
api_router.include_router(ai_leads.router, prefix="/ai", tags=["ai-leads"])
api_router.include_router(ai_deals.router, prefix="/ai", tags=["ai-deals"])


# Phase 3.1 replaced the attachment placeholders with real object storage;
# the endpoints live on the same /attachments router. See docs/DOCUMENTS.md.
