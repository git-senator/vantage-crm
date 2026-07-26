"""Property-intelligence endpoints.

The same two-tier shape as lead and deal intelligence: the deterministic quality
and the needs-attention ranking need only `properties.view` (computed from CRM
data and the Analytics Engine's market stats, with no egress); the generative
content needs `ai.use` and runs through the guarded `AIService`, so a budget
refusal is a 429 and a provider fault a 503.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.api.v1.dependencies import Authorization, CurrentUser, TenantSessionDep
from app.core.exceptions import RateLimitedError, ServiceUnavailableError
from app.schemas.property_intelligence import (
    ContentKind,
    NeedsAttentionListing,
    PropertyContentResponse,
    PropertyQualityRead,
    to_quality_read,
)
from app.services.ai.base import AIError, BudgetExceededError
from app.services.ai.property_intelligence import PropertyIntelligenceService

router = APIRouter()

MAX_NEEDS_ATTENTION = 50


@router.get("/properties/needs-attention", response_model=list[NeedsAttentionListing])
async def properties_needing_attention(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    limit: int = Query(20, ge=1, le=MAX_NEEDS_ATTENTION),
) -> list[NeedsAttentionListing]:
    """The caller's lowest-quality active listings, worst first, with the reasons.

    Deterministic and free. Reads stored quality under the caller's own
    `properties.view` scope, so it never surfaces a listing they could not open.
    """
    rows = await PropertyIntelligenceService(session, auth).needs_attention(limit=limit)
    await session.commit()
    return [
        NeedsAttentionListing(
            property_id=listing.id,
            title=listing.title,
            city=listing.city,
            status=listing.status,
            quality=quality.quality,
            grade=quality.grade,
            completeness=quality.completeness,
            top_reasons=quality.top_reasons,
        )
        for listing, quality in rows
    ]


@router.get("/properties/{property_id}/quality", response_model=PropertyQualityRead)
async def property_quality(
    property_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> PropertyQualityRead:
    """One listing's explainable quality, completeness and pricing insight.

    Deterministic. The pricing insight and staleness read use the Analytics
    Engine's market statistics. Computing refreshes the stored quality as a safe
    cache — the result is reproducible.
    """
    result = await PropertyIntelligenceService(session, auth).score(property_id)
    await session.commit()
    return to_quality_read(result)


@router.get("/properties/{property_id}/content", response_model=PropertyContentResponse)
async def property_content(
    property_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    kind: ContentKind = Query(..., description="summary, description, or seo"),
) -> PropertyContentResponse:
    """A generated piece of listing content — summary, description or SEO — plus
    the quality read it drew on. Requires `properties.view` and `ai.use`. The
    model writes copy grounded in the listing; it does not produce the score or
    change the listing."""
    service = PropertyIntelligenceService(session, auth)
    try:
        result, content = await service.generate(property_id, kind, user)
    except BudgetExceededError as exc:
        await session.commit()
        raise RateLimitedError(str(exc), retry_after=3600) from exc
    except AIError as exc:
        await session.commit()
        raise ServiceUnavailableError(str(exc)) from exc
    await session.commit()
    return PropertyContentResponse(
        kind=kind, content=content, quality=to_quality_read(result)
    )
