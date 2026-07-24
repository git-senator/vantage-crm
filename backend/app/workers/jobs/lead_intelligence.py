"""Background lead re-scoring.

A lead's score reflects its data, and its data changes — a call is logged, a
budget is added, contact goes quiet. Scoring on every read would be wasteful and
scoring never would let the prioritised list drift stale, so scores are
refreshed on the queue: on demand for one tenant, and nightly for all.

The whole thing is deterministic and needs no model — it runs the rule engine,
not `AIService` — so it is cheap, spends nothing, and cannot fail on a provider
outage. It is exactly the "rescore on activity via queue, not on read" the
roadmap called for, generalised to a nightly sweep so a workspace that never
opens a lead still has fresh rankings.

The job scores under a `system_context` holding `leads.view` at ALL scope, which
is the honest statement of what a tenant-wide rescore is; it writes only to
`lead_scores`, never to a lead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.logging import get_logger
from app.models.lead import Lead
from app.services.ai.lead_intelligence import LeadIntelligenceService
from app.workers.context import (
    active_organization_ids,
    system_context,
    tenant_scope,
    unscoped_scope,
)
from app.workers.runner import job

logger = get_logger(__name__)

#: The grant a rescore holds — reading leads, nothing wider. A sweep that could
#: edit a lead would be a scope the job has no use for.
_GRANTS: tuple[str, ...] = ("leads.view",)

#: How many leads one org's rescore touches in a run. A workspace with more open
#: leads than this catches the rest on the next nightly pass rather than one job
#: holding a worker for an unbounded stretch.
MAX_LEADS_PER_RUN = 2000


@job(max_tries=2)
async def rescore_leads(ctx: dict[str, Any]) -> int:
    """Refresh every active tenant's lead scores. Returns leads scored."""
    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    scored = 0
    for organization in organizations:
        scored += await _rescore_one(organization)

    if scored:
        logger.info("leads_rescored", extra={"leads": scored})
    return scored


@job(organization_arg=0, max_tries=2)
async def rescore_organization_leads(
    ctx: dict[str, Any], organization_id: str
) -> int:
    """Refresh one tenant's lead scores on demand."""
    return await _rescore_one(UUID(organization_id))


async def _rescore_one(organization_id: UUID) -> int:
    """Score every open lead in one tenant, bound to that tenant under RLS."""
    now = datetime.now(UTC)
    async with tenant_scope(organization_id) as session:
        auth = system_context(organization_id, *_GRANTS)
        service = LeadIntelligenceService(session, auth)

        # Open, non-deleted leads only: a lost or converted lead is terminal and
        # not something to rank for follow-up. RLS binds the tenant; this is the
        # business predicate on top.
        query = (
            select(Lead)
            .where(Lead.deleted_at.is_(None))
            .where(Lead.status == "open")
            .order_by(Lead.created_at.desc())
            .limit(MAX_LEADS_PER_RUN)
        )
        leads = list((await session.execute(query)).scalars().all())

        for lead in leads:
            # `_score_lead` extracts features (including a scoped activity count)
            # and upserts the score. Deterministic, so a re-run is a no-op on
            # unchanged data.
            await service.rescore(lead, now=now)

        await session.commit()

    return len(leads)
