"""Background deal re-scoring.

A deal's health reflects its data and its pipeline context, both of which move:
a stage changes, a close date passes, the pipeline's own mean-time-in-stage
drifts as deals flow through it. So health is refreshed on the queue — nightly
for all tenants, on demand for one — the same shape as lead re-scoring.

Deterministic and cheap: it runs the rule engine and reads the Analytics Engine's
stage velocity **once per tenant**, then scores every open deal against that one
map. No model, no spend, no dependency on a provider being up.

It scores under a `system_context` with `deals.view` at ALL scope and writes only
to `deal_scores`, never to a deal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.logging import get_logger
from app.models.deal import Deal
from app.models.pipeline import PipelineStage
from app.services.ai.deal_intelligence import DealIntelligenceService
from app.workers.context import (
    active_organization_ids,
    system_context,
    tenant_scope,
    unscoped_scope,
)
from app.workers.runner import job

logger = get_logger(__name__)

_GRANTS: tuple[str, ...] = ("deals.view",)

MAX_DEALS_PER_RUN = 2000


@job(max_tries=2)
async def rescore_deals(ctx: dict[str, Any]) -> int:
    """Refresh every active tenant's open-deal health. Returns deals scored."""
    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    scored = 0
    for organization in organizations:
        scored += await _rescore_one(organization)

    if scored:
        logger.info("deals_rescored", extra={"deals": scored})
    return scored


@job(organization_arg=0, max_tries=2)
async def rescore_organization_deals(
    ctx: dict[str, Any], organization_id: str
) -> int:
    """Refresh one tenant's open-deal health on demand."""
    return await _rescore_one(UUID(organization_id))


async def _rescore_one(organization_id: UUID) -> int:
    now = datetime.now(UTC)
    async with tenant_scope(organization_id) as session:
        auth = system_context(organization_id, *_GRANTS)
        service = DealIntelligenceService(session, auth)

        # One analytics read for the whole tenant — the pipeline norm every deal
        # is judged against, computed once rather than per deal.
        velocity = await service.velocity_map(now=now)

        # Open deals only: a closed deal's outcome is settled, not something to
        # keep scoring. Joined to the stage to filter terminal ones out.
        query = (
            select(Deal)
            .join(PipelineStage, PipelineStage.id == Deal.stage_id)
            .where(Deal.deleted_at.is_(None))
            .where(~PipelineStage.is_won)
            .where(~PipelineStage.is_lost)
            .order_by(Deal.created_at.desc())
            .limit(MAX_DEALS_PER_RUN)
        )
        deals = list((await session.execute(query)).unique().scalars().all())

        for deal in deals:
            await service.rescore(deal, velocity=velocity, now=now)

        await session.commit()

    return len(deals)
