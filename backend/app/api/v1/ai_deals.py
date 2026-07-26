"""Deal-intelligence endpoints.

The same two-tier shape as lead intelligence: the deterministic health and the
at-risk ranking need only `deals.view` (computed from CRM data, and from the
Analytics Engine's pipeline stats, with no egress); the generative narrative
needs `ai.use` and runs through the guarded `AIService`, so a budget refusal is a
429 and a provider fault a 503.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.api.v1.dependencies import Authorization, CurrentUser, TenantSessionDep
from app.core.exceptions import RateLimitedError, ServiceUnavailableError
from app.schemas.deal_intelligence import (
    AtRiskDeal,
    DealHealthRead,
    DealInsightResponse,
    to_health_read,
)
from app.services.ai.base import AIError, BudgetExceededError
from app.services.ai.deal_intelligence import DealIntelligenceService

router = APIRouter()

MAX_AT_RISK = 50


@router.get("/deals/at-risk", response_model=list[AtRiskDeal])
async def at_risk_deals(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    limit: int = Query(20, ge=1, le=MAX_AT_RISK),
) -> list[AtRiskDeal]:
    """The caller's lowest-health open deals, worst first, with the reasons why.

    Deterministic and free. Reads stored health under the caller's own
    `deals.view` scope, so it never surfaces a deal they could not open.
    """
    rows = await DealIntelligenceService(session, auth).at_risk(limit=limit)
    await session.commit()
    return [
        AtRiskDeal(
            deal_id=deal.id,
            title=deal.title,
            stage=deal.stage.name if deal.stage is not None else "",
            health=health.health,
            status=health.status,
            win_probability=health.win_probability,
            is_stalled=health.is_stalled,
            top_reasons=health.top_reasons,
        )
        for deal, health in rows
    ]


@router.get("/deals/{deal_id}/health", response_model=DealHealthRead)
async def deal_health(
    deal_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> DealHealthRead:
    """One deal's explainable health and win probability, from current data.

    Deterministic. Stalled detection and the time-in-stage signal use the
    Analytics Engine's pipeline norm. Computing refreshes the stored health as a
    safe cache — the result is reproducible.
    """
    result = await DealIntelligenceService(session, auth).score(deal_id)
    await session.commit()
    return to_health_read(result)


@router.get("/deals/{deal_id}/insights", response_model=DealInsightResponse)
async def deal_insights(
    deal_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> DealInsightResponse:
    """The health read plus a grounded AI narrative. Requires `deals.view` and
    `ai.use`. The model explains the numbers; it does not produce them."""
    service = DealIntelligenceService(session, auth)
    try:
        result, narrative = await service.insights(deal_id, user)
    except BudgetExceededError as exc:
        await session.commit()
        raise RateLimitedError(str(exc), retry_after=3600) from exc
    except AIError as exc:
        await session.commit()
        raise ServiceUnavailableError(str(exc)) from exc
    await session.commit()
    return DealInsightResponse(health=to_health_read(result), narrative=narrative)
