"""Growth-intelligence endpoints.

The same two-tier shape as the other intelligence modules: the deterministic
growth read needs only `reports.view` (computed from the Analytics Engine's own
aggregates, with no egress); the generative briefing needs `ai.use` and runs
through the guarded `AIService`, so a budget refusal is a 429 and a provider
fault a 503.

The period follows the analytics convention exactly — the same query parameters
every dashboard uses — so growth can be read for any window without a second
convention.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Query

from app.api.v1.dependencies import Authorization, CurrentUser, TenantSessionDep
from app.core.exceptions import RateLimitedError, ServiceUnavailableError
from app.schemas.analytics import PeriodName
from app.schemas.growth_intelligence import (
    GrowthBriefingResponse,
    GrowthHealthRead,
    to_growth_read,
)
from app.services.ai.base import AIError, BudgetExceededError
from app.services.ai.growth_intelligence import GrowthIntelligenceService
from app.services.analytics import Period, resolve_period

router = APIRouter()

DEFAULT_PERIOD: PeriodName = "month"


def get_period(
    period: PeriodName = Query(DEFAULT_PERIOD),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
) -> Period:
    """The window the growth read works in — the analytics convention, reused."""
    return resolve_period(period, start=start, end=end)


PeriodDep = Annotated[Period, Depends(get_period)]

#: The caller's UI language, from the same cookie the frontend writes. Drives the
#: language of the generated growth prose; absent/unknown falls back to English.
LocaleDep = Annotated[str | None, Cookie(alias="vg_locale")]


@router.get("/growth", response_model=GrowthHealthRead)
async def growth_health(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    period: PeriodDep,
    locale: LocaleDep = None,
) -> GrowthHealthRead:
    """The workspace's explainable growth score for the window.

    Deterministic. Every number is read from the Analytics Engine under the
    caller's own scope, so a manager sees their team's growth and an agent their
    own. Computing at organization-wide scope refreshes the stored canonical row
    as a safe cache — the result is reproducible.
    """
    result = await GrowthIntelligenceService(session, auth).score(period=period)
    await session.commit()
    return to_growth_read(result, period, locale or "en")


@router.get("/growth/briefing", response_model=GrowthBriefingResponse)
async def growth_briefing(
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    period: PeriodDep,
    locale: LocaleDep = None,
) -> GrowthBriefingResponse:
    """The growth read plus a grounded AI briefing. Requires `reports.view` and
    `ai.use`. The model explains the numbers; it does not produce them."""
    service = GrowthIntelligenceService(session, auth)
    try:
        result, narrative = await service.briefing(user, period=period)
    except BudgetExceededError as exc:
        await session.commit()
        raise RateLimitedError(str(exc), retry_after=3600) from exc
    except AIError as exc:
        await session.commit()
        raise ServiceUnavailableError(str(exc)) from exc
    await session.commit()
    return GrowthBriefingResponse(
        growth=to_growth_read(result, period, locale or "en"), narrative=narrative
    )
