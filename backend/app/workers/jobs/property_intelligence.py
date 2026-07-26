"""Background listing re-scoring.

A listing's quality reflects its own data and its market context, both of which
move: an agent fills in a description, the market's comparable prices drift, days
on market accrue. So quality is refreshed on the queue — nightly for all tenants,
on demand for one — the same shape as lead and deal re-scoring.

Deterministic and cheap: it runs the rule engine and reads the Analytics Engine's
market statistics **once per tenant**, then scores every active listing against
that one context. No model, no spend, no dependency on a provider being up.

It scores under a `system_context` with `properties.view` at ALL scope and writes
only to `property_scores`, never to a listing.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.logging import get_logger
from app.models.property import Property
from app.services.ai.property_intelligence import PropertyIntelligenceService
from app.workers.context import (
    active_organization_ids,
    system_context,
    tenant_scope,
    unscoped_scope,
)
from app.workers.runner import job

logger = get_logger(__name__)

_GRANTS: tuple[str, ...] = ("properties.view",)

MAX_PROPERTIES_PER_RUN = 2000


@job(max_tries=2)
async def rescore_properties(ctx: dict[str, Any]) -> int:
    """Refresh every active tenant's on-market listing quality. Returns the count."""
    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    scored = 0
    for organization in organizations:
        scored += await _rescore_one(organization)

    if scored:
        logger.info("properties_rescored", extra={"properties": scored})
    return scored


@job(organization_arg=0, max_tries=2)
async def rescore_organization_properties(
    ctx: dict[str, Any], organization_id: str
) -> int:
    """Refresh one tenant's on-market listing quality on demand."""
    return await _rescore_one(UUID(organization_id))


async def _rescore_one(organization_id: UUID) -> int:
    async with tenant_scope(organization_id) as session:
        auth = system_context(organization_id, *_GRANTS)
        service = PropertyIntelligenceService(session, auth)

        # One market read for the whole tenant — the comps and days-on-market
        # every listing is judged against, computed once rather than per listing.
        market = await service.market_context()

        # On-market listings only: a sold or off-market listing's quality is not
        # something to keep improving.
        query = (
            select(Property)
            .where(Property.deleted_at.is_(None))
            .where(Property.status.in_(("active", "pending", "coming_soon")))
            .order_by(Property.created_at.desc())
            .limit(MAX_PROPERTIES_PER_RUN)
        )
        listings = list((await session.execute(query)).unique().scalars().all())

        for listing in listings:
            await service.rescore(listing, market=market)

        await session.commit()

    return len(listings)
