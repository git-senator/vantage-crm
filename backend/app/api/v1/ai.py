"""AI infrastructure endpoints.

6.1 exposes only what the frontend needs to decide whether to *offer* AI, and
what an administrator needs to see it is not overspending. The endpoints that
actually run completions arrive with the features that use them (6.2+); shipping
a dispatch endpoint before a feature that needs one would be a surface with no
caller and a security review with no purpose.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.dependencies import Authorization, CurrentUser, TenantSessionDep
from app.schemas.ai import AiBudget, AiStatus, AiStatusResponse, AiUsageRow
from app.services.ai.service import AIService

router = APIRouter()


@router.get("/status", response_model=AiStatusResponse)
async def ai_status(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> AiStatusResponse:
    """Whether the AI layer is on, for this caller, and where its budget stands.

    Not gated on `ai.use`: a caller who *cannot* use AI still needs to be told
    that — the field is in the response — so the UI can hide the affordance
    rather than show a button that will 403. Any authenticated member may read
    it, and it discloses nothing beyond configuration and this tenant's own
    spend.
    """
    service = AIService(session, auth)
    return AiStatusResponse(
        status=AiStatus.model_validate(service.status()),
        budget=AiBudget.model_validate(await service.budget_status()),
    )


@router.get("/usage", response_model=list[AiUsageRow])
async def ai_usage(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> list[AiUsageRow]:
    """This month's AI spend per feature. Requires `ai.configure`.

    Gated tighter than status because it is a cost breakdown — a management
    view, like the report run history — rather than a feature-availability
    check.
    """
    rows = await AIService(session, auth).usage_summary()
    return [AiUsageRow.model_validate(row) for row in rows]
