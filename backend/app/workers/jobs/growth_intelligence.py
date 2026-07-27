"""Background growth recomputation.

A workspace's growth health reflects its aggregate metrics, which move every day
as leads, deals, listings and activity flow through the CRM. So the canonical
org-wide growth score is refreshed on the queue — nightly for all tenants, on
demand for one — the same shape as the per-record rescores.

Deterministic and cheap: it runs the rule engine over the Analytics Engine's own
aggregates. No model, no spend, no dependency on a provider being up.

It scores under a `system_context` at ALL scope — which is exactly the
organization-wide read the stored row is meant to hold — and writes only to
`growth_scores`, never to CRM data.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.services.ai.growth_intelligence import GrowthIntelligenceService
from app.workers.context import (
    active_organization_ids,
    system_context,
    tenant_scope,
    unscoped_scope,
)
from app.workers.runner import job

logger = get_logger(__name__)

#: Every entity grant the org-wide growth read spans, plus `reports.view` which
#: gates the read itself. All at ALL scope, so the job computes and stores the
#: canonical organization-wide score.
_GRANTS: tuple[str, ...] = (
    "reports.view",
    "leads.view",
    "deals.view",
    "properties.view",
    "contacts.view",
    "tasks.view",
    "activities.view",
)


@job(max_tries=2)
async def recompute_growth(ctx: dict[str, Any]) -> int:
    """Refresh every active tenant's growth score. Returns tenants scored."""
    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    scored = 0
    for organization in organizations:
        await _recompute_one(organization)
        scored += 1

    if scored:
        logger.info("growth_recomputed", extra={"tenants": scored})
    return scored


@job(organization_arg=0, max_tries=2)
async def recompute_organization_growth(
    ctx: dict[str, Any], organization_id: str
) -> int:
    """Refresh one tenant's growth score on demand."""
    await _recompute_one(UUID(organization_id))
    return 1


async def _recompute_one(organization_id: UUID) -> None:
    async with tenant_scope(organization_id) as session:
        auth = system_context(organization_id, *_GRANTS)
        # ALL scope ⇒ this is the canonical org-wide read; score() persists it.
        await GrowthIntelligenceService(session, auth).score()
        await session.commit()
