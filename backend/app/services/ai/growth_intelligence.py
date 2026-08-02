"""GrowthIntelligenceService — business health, built on the Analytics Engine.

The org-level member of the intelligence family (`lead_intelligence.py`,
`deal_intelligence.py`, `property_intelligence.py`). The same two-layer shape —
a deterministic engine plus an optional AI narrative — but where those score one
record, this scores the whole workspace, so its inputs are aggregates.

The reuse the brief requires is total here: **every number comes from the
Analytics Engine, none is recomputed.** The features are filled from
`AnalyticsService.kpis` (the same scope-resolved, previous-period-compared
metrics the dashboards render) and the pipeline breakdown (`pipeline_by_stage`
plus `stage_velocity`). This service reads them; it computes no metric of its own.

Scope is inherited, not redefined: `AnalyticsService` already resolves each
metric under its own entity grant, so a manager with a team scope is scored on
their team's numbers and an agent on their own — the exact rule the analytics
dashboards follow. The deterministic read needs `reports.view`; the narrative
needs `ai.use` through `AIService`.

The AI never writes CRM data. The score lives in `growth_scores`, one canonical
org-wide row per tenant, written only when the caller (or the nightly job) is
computing at organization-wide scope — a scoped, partial view is returned live
and never overwrites the canonical row.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

# Registers the growth-briefing prompt in PROMPTS at import.
from app.ai import growth_prompts as _growth_prompts
from app.ai.context import instruction
from app.ai.growth_scoring import (
    DEFAULT_SCORER,
    GrowthFeatures,
    GrowthHealth,
    GrowthScorer,
    StageSnapshot,
    score_growth,
)
from app.ai.prompts import ContentBlock
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.user import User
from app.repositories.analytics import AnalyticsRepository
from app.repositories.growth_score import GrowthScoreRepository
from app.services.ai.service import AIService
from app.services.analytics import AnalyticsService, MetricValue, Period, resolve_period
from app.services.rbac import AuthorizationContext

logger = get_logger(__name__)

FEATURE = "growth_briefing"

#: The window the pipeline stage velocity is measured over — long enough to be
#: stable, short enough to reflect how the pipeline moves now. Matches deal
#: intelligence.
_VELOCITY_WINDOW_DAYS = 180

#: The entity grants that must all resolve to ALL scope for a computation to be
#: the canonical organization-wide read worth persisting.
_ORG_WIDE_PERMISSIONS = (
    "leads.view",
    "deals.view",
    "properties.view",
    "contacts.view",
    "tasks.view",
    "activities.view",
)


def _f(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


class GrowthIntelligenceService:
    def __init__(
        self,
        session: AsyncSession,
        auth: AuthorizationContext,
        *,
        ai: AIService | None = None,
        scorer: GrowthScorer | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings or get_settings()
        self.scorer = scorer or DEFAULT_SCORER
        self.analytics = AnalyticsService(session, auth)
        self.repo = AnalyticsRepository(session)
        self.scores = GrowthScoreRepository(session)
        self._ai = ai

    @property
    def ai(self) -> AIService:
        if self._ai is None:
            self._ai = AIService(self.session, self.auth, settings=self.settings)
        return self._ai

    # ------------------------------------------------------- scoring

    async def score(self, *, period: Period | None = None) -> GrowthHealth:
        """Compute the workspace's growth health for the period. Requires
        `reports.view`. Persists the canonical row only when the caller is
        computing at organization-wide scope."""
        self.auth.require("reports.view")
        window = period or resolve_period("month")

        features = await self._extract_features(window)
        result = score_growth(features, self.scorer)

        if await self._is_org_wide():
            await self.scores.upsert(
                organization_id=self.auth.organization_id,
                score=result.score,
                band=result.band,
                period_start=window.start,
                period_end=window.end,
                breakdown=_breakdown(result),
                scorer=result.scorer,
            )
        return result

    async def _extract_features(self, period: Period) -> GrowthFeatures:
        """Fill the engine's inputs from the Analytics Engine. No metric is
        computed here — every number is read from `AnalyticsService`."""
        metrics = {m.key: m for m in await self.analytics.kpis(period)}
        stages = await self._pipeline_stages(period)

        def value(key: str) -> Decimal | None:
            m = metrics.get(key)
            return m.value if m is not None else None

        def count(key: str) -> int:
            v = value(key)
            return int(v) if v is not None else 0

        def delta(key: str) -> float | None:
            m = metrics.get(key)
            return _f(m.delta_percent) if m is not None else None

        def previous(key: str) -> Decimal | None:
            m = metrics.get(key)
            return m.previous if m is not None else None

        return GrowthFeatures(
            lead_conversion_rate=_f(value("lead_conversion_rate")),
            win_rate=_f(value("win_rate")),
            sales_cycle_days=_f(value("sales_cycle_days")),
            revenue_won=value("revenue_won") or Decimal(0),
            revenue_delta_pct=delta("revenue_won"),
            prior_revenue_won=previous("revenue_won"),
            deals_won=count("deals_won"),
            deals_lost=count("deals_lost"),
            deals_created=count("deals_created"),
            leads_created=count("leads_created"),
            leads_converted=count("leads_converted"),
            pipeline_open_value=value("pipeline_open_value") or Decimal(0),
            pipeline_weighted_value=value("pipeline_weighted_value") or Decimal(0),
            average_deal_value=value("average_deal_value"),
            commission_earned=value("commission_earned") or Decimal(0),
            activities_logged=count("activities_logged"),
            activities_delta_pct=delta("activities_logged"),
            tasks_completed=count("tasks_completed"),
            tasks_overdue=count("tasks_overdue"),
            listings_active=count("listings_active"),
            listings_sold=count("listings_sold"),
            pipeline_stages=stages,
            period_label=period.label,
        )

    async def _pipeline_stages(self, period: Period) -> tuple[StageSnapshot, ...]:
        """The open pipeline by stage, with each stage's velocity — both from the
        Analytics Engine, scoped by the caller's `deals.view` grant. Empty when
        the caller cannot see deals."""
        deals = await self.analytics.scope_for("deals.view")
        if deals is False:
            return ()

        owner_ids = deals if deals is not None else None
        by_stage = await self.repo.pipeline_by_stage(
            self.auth.organization_id, owner_ids
        )
        velocity = await self.repo.stage_velocity(
            self.auth.organization_id,
            period.end - timedelta(days=_VELOCITY_WINDOW_DAYS),
            period.end,
        )
        mean_days = {stage_id: float(days) for stage_id, _n, days, _c in velocity}

        return tuple(
            StageSnapshot(
                name=name,
                deal_count=count,
                open_value=value,
                mean_days_in_stage=mean_days.get(stage_id),
            )
            for stage_id, name, count, value in by_stage
        )

    async def _is_org_wide(self) -> bool:
        """True when every entity grant resolves to ALL scope — the only case in
        which the live computation is the canonical org-wide read worth storing."""
        for permission in _ORG_WIDE_PERMISSIONS:
            if await self.analytics.scope_for(permission) is not None:
                return False
        return True

    # --------------------------------------------------- ai narrative

    async def briefing(
        self, actor: User, *, period: Period | None = None
    ) -> tuple[GrowthHealth, str]:
        """The deterministic growth read plus a grounded AI briefing. Requires
        `reports.view` and `ai.use`. The model explains the numbers; it does not
        produce them."""
        self.auth.require("reports.view")
        self.auth.require("ai.use")
        window = period or resolve_period("month")

        result = await self.score(period=window)

        blocks: list[ContentBlock] = [
            instruction("The CRM's growth analysis of this workspace:"),
            instruction(_render_analysis(result, window)),
        ]
        request = _growth_prompts.GROWTH_BRIEFING_PROMPT.build(
            blocks,
            model=self.settings.AI_MODEL,
            max_tokens=self.settings.AI_MAX_OUTPUT_TOKENS,
            metadata={"period": window.label},
        )
        completion = await self.ai.complete(request, feature=FEATURE, actor=actor)
        return result, completion.text


def _breakdown(result: GrowthHealth) -> dict:  # type: ignore[type-arg]
    return {
        "signals": [
            {"key": s.key, "label": s.label, "points": s.points, "reason": s.reason}
            for s in result.signals
        ],
        "revenue_signals": [s.text for s in result.revenue_signals],
        "pipeline_insights": [s.text for s in result.pipeline_insights],
        "risks": [
            {"key": r.key, "label": r.label, "detail": r.detail} for r in result.risks
        ],
        "recommendations": [
            {"action": r.action, "reason": r.reason, "priority": r.priority}
            for r in result.recommendations
        ],
        "top_reasons": result.top_reasons,
    }


def _render_analysis(result: GrowthHealth, period: Period) -> str:
    """A compact, factual rendering of the growth read for the model to narrate.
    Trusted text — derived aggregates only, no raw customer records."""
    lines = [
        f"Growth score: {result.score}/100 ({result.band}) for {period.label}.",
        "Signals:",
    ]
    lines += [f"- {s.reason} ({s.points:+d})" for s in result.signals]
    if result.revenue_signals:
        lines.append("Revenue:")
        lines += [f"- {s.text}" for s in result.revenue_signals]
    if result.pipeline_insights:
        lines.append("Pipeline:")
        lines += [f"- {s.text}" for s in result.pipeline_insights]
    if result.risks:
        lines.append("Risks:")
        lines += [f"- {r.label}: {r.detail}" for r in result.risks]
    if result.recommendations:
        lines.append("Recommended priorities:")
        lines += [f"- {r.action}: {r.reason}" for r in result.recommendations]
    return "\n".join(lines)


# Re-exported so `MetricValue` need not be imported from two places by callers
# that build on this service.
__all__ = [
    "FEATURE",
    "GrowthIntelligenceService",
    "MetricValue",
]
