"""DealIntelligenceService — deal health, and the Analytics Engine reuse.

The deal counterpart of `lead_intelligence.py`, with one extra integration that
the brief asked for by name: **pipeline statistics come from the Analytics
Engine, not from a second copy of the logic here.** Stalled detection and the
"time in stage" signal compare a deal's days-in-current-stage against the
pipeline's mean-time-in-stage — and that mean is `AnalyticsRepository.stage_
velocity`, the same statistic the analytics dashboards render. This module reads
it, it does not recompute it.

Everything else mirrors lead intelligence. Reads go through scoped paths:

  * the deal through `DealService.get_deal`, which 404s a deal outside scope;
  * its current-stage age through `DealStageHistoryRepository.latest_for_deal`;
  * the pipeline norm through the Analytics Engine, pipeline-wide by design.

Two permission tiers: the deterministic health needs only `deals.view`; the
generative narrative needs `ai.use` and runs through `AIService`. The AI never
writes to the deal — health lives in `deal_scores`, the deal's own `probability`
is the agent's.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

# Registers the deal-insight prompt in PROMPTS at import.
from app.ai import deal_prompts as _deal_prompts
from app.ai.context import instruction
from app.ai.deal_scoring import (
    DEFAULT_SCORER,
    DealFeatures,
    DealHealth,
    DealScorer,
    score_deal,
)
from app.ai.prompts import ContentBlock
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.deal import Deal
from app.models.user import User
from app.repositories.analytics import AnalyticsRepository
from app.repositories.deal import DealStageHistoryRepository
from app.repositories.deal_score import DealScoreRepository
from app.services.ai.context_builders import build_entity_context
from app.services.ai.service import AIService
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

FEATURE = "deal_insight"

#: The window the pipeline norm is measured over. Long enough that a stage's mean
#: time is stable, short enough that it reflects how the pipeline moves *now*
#: rather than a year ago.
_VELOCITY_WINDOW_DAYS = 180


def extract_features(
    deal: Deal,
    *,
    days_in_current_stage: int,
    pipeline_mean_days_in_stage: float | None,
    now: datetime,
) -> DealFeatures:
    """Map a scoped deal to the engine's inputs. Pure given its arguments."""
    stage = deal.stage
    today = now.date()
    close = deal.expected_close_date
    created = deal.created_at or now
    updated = deal.updated_at or created
    return DealFeatures(
        stage_name=stage.name if stage is not None else "unknown",
        stage_position=stage.position if stage is not None else 0,
        is_won=bool(stage is not None and stage.is_won),
        is_lost=bool(stage is not None and stage.is_lost),
        probability=deal.probability,
        priority=deal.priority,
        has_value=deal.value is not None,
        value=deal.value,
        has_property=deal.property_id is not None,
        has_commission=deal.commission_amount is not None,
        days_to_expected_close=(close - today).days if close is not None else None,
        days_in_current_stage=days_in_current_stage,
        days_since_created=max(0, (now - created).days),
        days_since_updated=max(0, (now - updated).days),
        pipeline_mean_days_in_stage=pipeline_mean_days_in_stage,
    )


def _breakdown(result: DealHealth) -> dict:  # type: ignore[type-arg]
    return {
        "signals": [
            {"key": s.key, "label": s.label, "points": s.points, "reason": s.reason}
            for s in result.signals
        ],
        "probability_factors": [
            {"key": s.key, "label": s.label, "points": s.points, "reason": s.reason}
            for s in result.probability_factors
        ],
        "risks": [
            {"key": r.key, "label": r.label, "detail": r.detail} for r in result.risks
        ],
        "missing_info": [
            {"key": m.key, "label": m.label} for m in result.missing_info
        ],
        "recommendations": [
            {"action": r.action, "reason": r.reason, "priority": r.priority}
            for r in result.recommendations
        ],
        "top_reasons": result.top_reasons,
    }


class DealIntelligenceService:
    def __init__(
        self,
        session: AsyncSession,
        auth: AuthorizationContext,
        *,
        ai: AIService | None = None,
        scorer: DealScorer | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings or get_settings()
        self.scorer = scorer or DEFAULT_SCORER
        self.history = DealStageHistoryRepository(session)
        self.analytics = AnalyticsRepository(session)
        self.scores = DealScoreRepository(session)
        self.rbac = RbacService(session)
        self._ai = ai

    @property
    def ai(self) -> AIService:
        if self._ai is None:
            self._ai = AIService(self.session, self.auth, settings=self.settings)
        return self._ai

    # ------------------------------------------------ analytics reuse

    async def velocity_map(self, *, now: datetime | None = None) -> dict[UUID, float]:
        """Pipeline mean days-in-stage, per stage, from the Analytics Engine.

        The whole map in one call, so scoring a book of deals reuses one
        analytics read rather than one per deal. This is the reuse the brief
        requires: the norm a deal is judged stalled against is the same number
        the analytics dashboards show, computed in one place.
        """
        moment = now or datetime.now(UTC)
        rows = await self.analytics.stage_velocity(
            self.auth.organization_id,
            moment - timedelta(days=_VELOCITY_WINDOW_DAYS),
            moment,
        )
        return {stage_id: float(mean_days) for stage_id, _name, mean_days, _n in rows}

    # ------------------------------------------------------- scoring

    async def score(self, deal_id: UUID, *, now: datetime | None = None) -> DealHealth:
        """Score one deal's health and persist it. Requires `deals.view`."""
        from app.services.deal import DealService

        self.auth.require("deals.view")
        moment = now or datetime.now(UTC)

        deal = await DealService(self.session, self.auth).get_deal(deal_id)
        velocity = await self.velocity_map(now=moment)
        return await self._score_deal(deal, velocity, moment)

    async def rescore(
        self,
        deal: Deal,
        *,
        velocity: dict[UUID, float],
        now: datetime | None = None,
    ) -> DealHealth:
        """Score an already-fetched deal against a precomputed velocity map. For
        the background sweep, which reads the map once per tenant and every deal
        under a system context."""
        return await self._score_deal(deal, velocity, now or datetime.now(UTC))

    async def _score_deal(
        self, deal: Deal, velocity: dict[UUID, float], now: datetime
    ) -> DealHealth:
        latest = await self.history.latest_for_deal(deal.id, self.auth.organization_id)
        anchor = latest.changed_at if latest is not None else deal.created_at
        days_in_stage = max(0, (now - (anchor or now)).days)

        features = extract_features(
            deal,
            days_in_current_stage=days_in_stage,
            pipeline_mean_days_in_stage=velocity.get(deal.stage_id),
            now=now,
        )
        result = score_deal(features, self.scorer)

        forecast = (
            None if result.forecast_value is None else _decimal(result.forecast_value)
        )
        await self.scores.upsert(
            organization_id=self.auth.organization_id,
            deal_id=deal.id,
            health=result.health,
            status=result.status,
            win_probability=result.win_probability,
            forecast_value=forecast,
            is_stalled=result.is_stalled,
            breakdown=_breakdown(result),
            scorer=result.scorer,
        )
        return result

    async def at_risk(self, *, limit: int = 20) -> list[tuple[Deal, DealHealth]]:
        """The caller's lowest-health open deals, worst first, with reasons."""
        scope = self.auth.require("deals.view")
        owner_ids = await self.rbac.owner_ids_for_scope(self.auth, scope)

        rows = await self.scores.at_risk(
            self.auth.organization_id, owner_ids=owner_ids, limit=limit
        )
        from app.services.deal import DealService

        deal_service = DealService(self.session, self.auth)
        out: list[tuple[Deal, DealHealth]] = []
        for row in rows:
            deal = await deal_service.get_deal(row.deal_id)
            out.append((deal, _reconstruct(row)))
        return out

    # --------------------------------------------------- ai narrative

    async def insights(
        self, deal_id: UUID, actor: User, *, now: datetime | None = None
    ) -> tuple[DealHealth, str]:
        """Deterministic health plus a grounded AI narrative. Requires `ai.use`."""
        from app.services.deal import DealService

        self.auth.require("deals.view")
        self.auth.require("ai.use")
        moment = now or datetime.now(UTC)

        deal = await DealService(self.session, self.auth).get_deal(deal_id)
        velocity = await self.velocity_map(now=moment)
        result = await self._score_deal(deal, velocity, moment)

        blocks: list[ContentBlock] = [
            instruction("The CRM's analysis of this deal:"),
            instruction(_render_analysis(result)),
        ]
        context = await build_entity_context(self.session, self.auth, "deal", deal_id)
        if context is not None:
            blocks.extend(context)

        request = _deal_prompts.DEAL_INSIGHT_PROMPT.build(
            blocks,
            model=self.settings.AI_MODEL,
            max_tokens=self.settings.AI_MAX_OUTPUT_TOKENS,
            metadata={"deal": str(deal_id)},
        )
        completion = await self.ai.complete(request, feature=FEATURE, actor=actor)
        return result, completion.text


def _render_analysis(result: DealHealth) -> str:
    """A compact, factual rendering of the health read for the model to narrate.
    Trusted text — derived facts only, no raw PII."""
    lines = [
        f"Health: {result.health}/100 ({result.status}).",
        f"Win probability: {result.win_probability}%"
        + (f", forecast contribution {result.forecast_value}." if result.forecast_value else "."),
        "How the probability was reached:",
    ]
    lines += [f"- {s.reason} ({s.points:+d})" for s in result.probability_factors]
    lines.append("Health signals:")
    lines += [f"- {s.reason} ({s.points:+d})" for s in result.signals]
    if result.risks:
        lines.append("Risks:")
        lines += [f"- {r.label}: {r.detail}" for r in result.risks]
    if result.recommendations:
        lines.append("Recommended actions:")
        lines += [f"- {r.action}: {r.reason}" for r in result.recommendations]
    return "\n".join(lines)


def _decimal(value: str):  # type: ignore[no-untyped-def]
    from decimal import Decimal

    return Decimal(value)


def _reconstruct(row) -> DealHealth:  # type: ignore[no-untyped-def]
    from app.ai.explain import (
        MissingField,
        Recommendation,
        RiskFlag,
        ScoredSignal,
    )

    b = row.breakdown or {}
    return DealHealth(
        health=row.health,
        status=row.status,
        win_probability=row.win_probability,
        forecast_value=str(row.forecast_value) if row.forecast_value is not None else None,
        is_stalled=row.is_stalled,
        signals=[
            ScoredSignal(s["key"], s["label"], s["points"], s["reason"])
            for s in b.get("signals", [])
        ],
        probability_factors=[
            ScoredSignal(s["key"], s["label"], s["points"], s["reason"])
            for s in b.get("probability_factors", [])
        ],
        risks=[RiskFlag(r["key"], r["label"], r["detail"]) for r in b.get("risks", [])],
        missing_info=[
            MissingField(m["key"], m["label"]) for m in b.get("missing_info", [])
        ],
        recommendations=[
            Recommendation(r["action"], r["reason"], r["priority"])
            for r in b.get("recommendations", [])
        ],
        scorer=row.scorer,
    )


__all__ = ["FEATURE", "DealIntelligenceService", "extract_features"]
