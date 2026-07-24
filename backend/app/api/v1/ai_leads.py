"""Lead-intelligence endpoints.

Two permission tiers, mirroring the service. The deterministic reads — score and
prioritisation — need only `leads.view`, because they are computed from CRM data
with no egress and work with the AI layer off. The generative narrative needs
`ai.use` and runs through `AIService`, so a budget refusal is a 429 and a
provider fault a 503, the same posture as the assistant.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.api.v1.dependencies import Authorization, CurrentUser, TenantSessionDep
from app.core.exceptions import RateLimitedError, ServiceUnavailableError
from app.schemas.lead_intelligence import (
    LeadInsightResponse,
    LeadScoreRead,
    PrioritisedLead,
    to_score_read,
)
from app.services.ai.base import AIError, BudgetExceededError
from app.services.ai.lead_intelligence import LeadIntelligenceService

router = APIRouter()

#: Bounded so a prioritised list stays a shortlist. A hundred "top" leads is not
#: a priority list, it is the lead list with extra steps.
MAX_PRIORITISED = 50


@router.get("/leads/prioritized", response_model=list[PrioritisedLead])
async def prioritized_leads(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    limit: int = Query(20, ge=1, le=MAX_PRIORITISED),
) -> list[PrioritisedLead]:
    """The caller's highest-scoring open leads, ranked, with the reasons why.

    Deterministic and free — no model call. Reads stored scores under the
    caller's own `leads.view` scope, so it never surfaces a lead they could not
    open in the list.
    """
    rows = await LeadIntelligenceService(session, auth).prioritized(limit=limit)
    # A rescore may have been written while ranking; commit it so the stored
    # scores stay current for the next reader.
    await session.commit()
    return [
        PrioritisedLead(
            lead_id=lead.id,
            full_name=f"{lead.first_name} {lead.last_name}".strip(),
            stage=lead.stage,
            score=score.score,
            temperature=score.temperature,
            priority=score.priority,
            buying_intent=score.buying_intent,
            top_reasons=score.top_reasons,
        )
        for lead, score in rows
    ]


@router.get("/leads/{lead_id}/score", response_model=LeadScoreRead)
async def lead_score(
    lead_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> LeadScoreRead:
    """One lead's explainable score, computed from its current data.

    Deterministic — the same lead always scores the same. Computing refreshes the
    stored score as a side effect, which is safe precisely because the result is
    reproducible: the write is a cache, not a decision.
    """
    result = await LeadIntelligenceService(session, auth).score(lead_id)
    await session.commit()
    return to_score_read(result)


@router.get("/leads/{lead_id}/insights", response_model=LeadInsightResponse)
async def lead_insights(
    lead_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> LeadInsightResponse:
    """The score plus a grounded AI narrative. Requires `leads.view` and `ai.use`.

    The narrative explains the deterministic score; the model does not produce
    the number. Every call is on the AI ledger and audited as egress.
    """
    service = LeadIntelligenceService(session, auth)
    try:
        result, narrative = await service.insights(lead_id, user)
    except BudgetExceededError as exc:
        await session.commit()  # the refusal is on the ledger; keep it
        raise RateLimitedError(str(exc), retry_after=3600) from exc
    except AIError as exc:
        await session.commit()
        raise ServiceUnavailableError(str(exc)) from exc
    await session.commit()
    return LeadInsightResponse(score=to_score_read(result), narrative=narrative)
