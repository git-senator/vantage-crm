"""Aggregate router for API v1.

One aggregation point so `main.py` does not accumulate a list of imports as the
surface grows.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    access_requests,
    activities,
    admin,
    ai,
    ai_assistant,
    ai_deals,
    ai_growth,
    ai_leads,
    ai_properties,
    analytics,
    api_keys,
    attachments,
    attention,
    audit,
    auth,
    automations,
    billing,
    calendar,
    clients,
    compliance_ops,
    conversations,
    dashboard,
    deals,
    developer,
    enterprise,
    governance,
    integrations,
    jobs,
    leads,
    marketplace,
    marketplace_admin,
    marketplace_billing,
    marketplace_developer,
    marketplace_sdk,
    notes,
    notifications,
    organizations,
    pipelines,
    plugins,
    properties,
    reports,
    resilience,
    roles,
    sdk,
    security_ops,
    tasks,
    timeline,
    trust,
    whatsapp,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(
    access_requests.router, prefix="/access-requests", tags=["access-requests"]
)
api_router.include_router(
    organizations.router, prefix="/organizations", tags=["organizations"]
)
api_router.include_router(roles.router, prefix="/roles", tags=["roles"])
api_router.include_router(api_keys.router, prefix="/api-keys", tags=["api-keys"])
api_router.include_router(developer.router, prefix="/developer", tags=["developer"])
api_router.include_router(
    integrations.router, prefix="/integrations", tags=["integrations"]
)
api_router.include_router(
    enterprise.router, prefix="/enterprise", tags=["enterprise"]
)
api_router.include_router(
    security_ops.router, prefix="/security", tags=["security-ops"]
)
api_router.include_router(
    compliance_ops.router, prefix="/compliance", tags=["compliance-ops"]
)
api_router.include_router(trust.router, prefix="/trust", tags=["trust"])
api_router.include_router(
    governance.router, prefix="/governance", tags=["governance"]
)
api_router.include_router(
    resilience.router, prefix="/resilience", tags=["resilience"]
)
api_router.include_router(plugins.router, prefix="/plugins", tags=["plugins"])
api_router.include_router(sdk.router, prefix="/sdk", tags=["sdk"])
api_router.include_router(
    marketplace.router, prefix="/marketplace", tags=["marketplace"]
)
api_router.include_router(
    marketplace_admin.router,
    prefix="/marketplace/admin",
    tags=["marketplace-admin"],
)
api_router.include_router(
    marketplace_billing.router,
    prefix="/marketplace/billing",
    tags=["marketplace-billing"],
)
api_router.include_router(
    marketplace_developer.router,
    prefix="/marketplace",
    tags=["marketplace-developer"],
)
api_router.include_router(
    marketplace_sdk.router,
    prefix="/marketplace",
    tags=["marketplace-sdk"],
)
api_router.include_router(audit.router, prefix="/audit-logs", tags=["audit"])
api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
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
api_router.include_router(attention.router, prefix="/attention", tags=["attention"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(ai_assistant.router, prefix="/ai", tags=["ai-assistant"])
api_router.include_router(ai_leads.router, prefix="/ai", tags=["ai-leads"])
api_router.include_router(ai_deals.router, prefix="/ai", tags=["ai-deals"])
api_router.include_router(ai_properties.router, prefix="/ai", tags=["ai-properties"])
api_router.include_router(ai_growth.router, prefix="/ai", tags=["ai-growth"])


# Phase 3.1 replaced the attachment placeholders with real object storage;
# the endpoints live on the same /attachments router. See docs/DOCUMENTS.md.
