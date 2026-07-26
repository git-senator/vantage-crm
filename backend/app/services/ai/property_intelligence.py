"""PropertyIntelligenceService — listing quality, and the Analytics Engine reuse.

The property counterpart of `lead_intelligence.py` and `deal_intelligence.py`,
with the same integration the brief asks for by name: **market statistics come
from the Analytics Engine, not from a second copy of the logic here.** The
pricing insight compares a listing's price-per-square-foot against the market's
comparable median, and the staleness read against the market's average days on
market — and both come from `AnalyticsRepository`, the same aggregates the
analytics dashboards render. This module reads them, it does not recompute them.

Everything else mirrors lead and deal intelligence. Reads go through scoped
paths:

  * the listing through `PropertyService.get_property`, which applies the
    shared-inventory scope (view is org-wide; a listing outside the tenant 404s);
  * the market comps and days-on-market through the Analytics Engine, org-wide by
    design — a fair benchmark is the whole market's.

Two permission tiers: the deterministic quality needs only `properties.view`; the
generative content (summary, description, SEO) needs `ai.use` and runs through
`AIService`. The AI never writes to the listing — quality lives in
`property_scores`, the listing's own fields are the agent's.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

# Registers the property-content prompts in PROMPTS at import.
from app.ai import property_prompts as _property_prompts
from app.ai.context import instruction
from app.ai.prompts import ContentBlock
from app.ai.property_scoring import (
    DEFAULT_SCORER,
    PropertyFeatures,
    PropertyQuality,
    PropertyScorer,
    score_property,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.models.property import Property
from app.models.user import User
from app.repositories.analytics import AnalyticsRepository
from app.repositories.property_score import PropertyScoreRepository
from app.services.ai.context_builders import build_entity_context
from app.services.ai.service import AIService
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)


class MarketContext:
    """The tenant-wide market aggregates a listing is judged against, read once
    from the Analytics Engine and reused across every listing in a batch.

    The property analog of the deal engine's velocity map: the norm every listing
    is compared to, computed in one place rather than per listing.
    """

    __slots__ = ("avg_days_on_market", "price_benchmarks")

    def __init__(
        self,
        price_benchmarks: dict[str, tuple[Decimal, int]],
        avg_days_on_market: float | None,
    ) -> None:
        self.price_benchmarks = price_benchmarks
        self.avg_days_on_market = avg_days_on_market


def extract_features(
    listing: Property, market: MarketContext
) -> PropertyFeatures:
    """Map a scoped listing plus the market context to the engine's inputs. Pure
    given its arguments."""
    median, sample = market.price_benchmarks.get(
        listing.property_type, (None, 0)
    )
    return PropertyFeatures(
        property_type=listing.property_type,
        status=listing.status,
        description_length=len(listing.description or ""),
        feature_count=len(listing.features or []),
        has_price=listing.price is not None,
        price=listing.price,
        has_bedrooms=listing.bedrooms is not None,
        has_bathrooms=listing.bathrooms is not None,
        has_square_feet=listing.square_feet is not None,
        square_feet=listing.square_feet,
        has_year_built=listing.year_built is not None,
        has_lot_size=listing.lot_size_sqft is not None,
        has_geo=listing.latitude is not None and listing.longitude is not None,
        has_mls=bool(listing.mls_number),
        days_on_market=listing.days_on_market,
        comp_median_price_per_sqft=median,
        comp_sample_size=sample,
        market_avg_days_on_market=market.avg_days_on_market,
    )


def _breakdown(result: PropertyQuality) -> dict:  # type: ignore[type-arg]
    return {
        "signals": [
            {"key": s.key, "label": s.label, "points": s.points, "reason": s.reason}
            for s in result.signals
        ],
        "pricing": {
            "stance": result.pricing.stance,
            "delta_pct": result.pricing.delta_pct,
            "benchmark": result.pricing.benchmark,
            "sample_size": result.pricing.sample_size,
            "reason": result.pricing.reason,
        },
        "strengths": result.strengths,
        "weaknesses": result.weaknesses,
        "missing_info": [
            {"key": m.key, "label": m.label} for m in result.missing_info
        ],
        "recommendations": [
            {"action": r.action, "reason": r.reason, "priority": r.priority}
            for r in result.recommendations
        ],
        "top_reasons": result.top_reasons,
    }


class PropertyIntelligenceService:
    def __init__(
        self,
        session: AsyncSession,
        auth: AuthorizationContext,
        *,
        ai: AIService | None = None,
        scorer: PropertyScorer | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings or get_settings()
        self.scorer = scorer or DEFAULT_SCORER
        self.analytics = AnalyticsRepository(session)
        self.scores = PropertyScoreRepository(session)
        self.rbac = RbacService(session)
        self._ai = ai

    @property
    def ai(self) -> AIService:
        if self._ai is None:
            self._ai = AIService(self.session, self.auth, settings=self.settings)
        return self._ai

    # ------------------------------------------------ analytics reuse

    async def market_context(self) -> MarketContext:
        """The tenant's market aggregates, from the Analytics Engine, in one read
        per statistic — the price-per-sqft comps per type and the average days on
        market. This is the reuse the brief requires: the numbers a listing is
        judged against are the same ones the analytics dashboards show, computed
        in one place, not duplicated here.
        """
        benchmarks = await self.analytics.price_benchmarks(self.auth.organization_id)
        # Org-wide (owner_ids None): the market norm, not one agent's slice.
        avg_dom = await self.analytics.average_days_on_market(
            self.auth.organization_id, None
        )
        return MarketContext(
            benchmarks, float(avg_dom) if avg_dom is not None else None
        )

    # ------------------------------------------------------- scoring

    async def score(self, property_id: UUID) -> PropertyQuality:
        """Score one listing's quality and persist it. Requires `properties.view`."""
        from app.services.property import PropertyService

        self.auth.require("properties.view")
        listing = await PropertyService(self.session, self.auth).get_property(
            property_id
        )
        market = await self.market_context()
        return await self._score_listing(listing, market)

    async def rescore(
        self, listing: Property, *, market: MarketContext
    ) -> PropertyQuality:
        """Score an already-fetched listing against a precomputed market context.
        For the background sweep, which reads the market once per tenant and every
        listing under a system context."""
        return await self._score_listing(listing, market)

    async def _score_listing(
        self, listing: Property, market: MarketContext
    ) -> PropertyQuality:
        features = extract_features(listing, market)
        result = score_property(features, self.scorer)

        await self.scores.upsert(
            organization_id=self.auth.organization_id,
            property_id=listing.id,
            quality=result.quality,
            grade=result.grade,
            completeness=result.completeness,
            breakdown=_breakdown(result),
            scorer=result.scorer,
        )
        return result

    async def needs_attention(
        self, *, limit: int = 20
    ) -> list[tuple[Property, PropertyQuality]]:
        """The caller's lowest-quality active listings, worst first, with reasons."""
        scope = self.auth.require("properties.view")
        agent_ids = await self.rbac.owner_ids_for_scope(self.auth, scope)

        rows = await self.scores.needs_attention(
            self.auth.organization_id, agent_ids=agent_ids, limit=limit
        )
        from app.services.property import PropertyService

        service = PropertyService(self.session, self.auth)
        out: list[tuple[Property, PropertyQuality]] = []
        for row in rows:
            listing = await service.get_property(row.property_id)
            out.append((listing, _reconstruct(row)))
        return out

    # --------------------------------------------------- ai content

    async def generate(
        self, property_id: UUID, kind: str, actor: User
    ) -> tuple[PropertyQuality, str]:
        """Deterministic quality plus a grounded, generated piece of listing
        content — a summary, a description, or SEO suggestions. Requires `ai.use`.

        The quality is computed and persisted first (the deterministic half). The
        copy is then generated through `AIService` from the listing's fenced,
        redacted context plus the CRM's own strengths — the model writes copy, it
        never produces the score and never writes to the listing.
        """
        from app.services.property import PropertyService

        self.auth.require("properties.view")
        self.auth.require("ai.use")

        entry = _property_prompts.CONTENT_PROMPTS.get(kind)
        if entry is None:
            raise AppError(
                f"Unknown content kind '{kind}'. "
                f"Expected one of: {', '.join(sorted(_property_prompts.CONTENT_PROMPTS))}."
            )
        prompt, feature = entry

        listing = await PropertyService(self.session, self.auth).get_property(
            property_id
        )
        market = await self.market_context()
        result = await self._score_listing(listing, market)

        # Trusted content: the CRM's own read — derived facts and generic reasons,
        # no raw PII — to steer the copy toward the real strengths.
        blocks: list[ContentBlock] = [
            instruction("The CRM's analysis of this listing:"),
            instruction(_render_analysis(result)),
        ]
        # The listing's own fields arrive as fenced, redacted context, rebuilt
        # under scope through the same builder the assistant uses. Access is
        # already proven by the get_property above, so this cannot leak.
        context = await build_entity_context(
            self.session, self.auth, "property", property_id
        )
        if context is not None:
            blocks.extend(context)

        request = prompt.build(
            blocks,
            model=self.settings.AI_MODEL,
            max_tokens=self.settings.AI_MAX_OUTPUT_TOKENS,
            metadata={"property": str(property_id), "kind": kind},
        )
        completion = await self.ai.complete(request, feature=feature, actor=actor)
        return result, completion.text


def _render_analysis(result: PropertyQuality) -> str:
    """A compact, factual rendering of the quality read for the model to work
    from. Trusted text — derived facts only, no raw PII."""
    lines = [
        f"Listing quality: {result.quality}/100 ({result.grade}).",
        f"Completeness: {result.completeness}%.",
    ]
    if result.strengths:
        lines.append("Strengths:")
        lines += [f"- {s}" for s in result.strengths]
    if result.weaknesses:
        lines.append("Weaknesses:")
        lines += [f"- {w}" for w in result.weaknesses]
    lines.append(f"Pricing: {result.pricing.reason}")
    return "\n".join(lines)


def _reconstruct(row) -> PropertyQuality:  # type: ignore[no-untyped-def]
    from app.ai.explain import MissingField, Recommendation, ScoredSignal
    from app.ai.property_scoring import PricingInsight

    b = row.breakdown or {}
    p = b.get("pricing", {})
    return PropertyQuality(
        quality=row.quality,
        grade=row.grade,
        completeness=row.completeness,
        pricing=PricingInsight(
            stance=p.get("stance", "unknown"),
            delta_pct=p.get("delta_pct"),
            benchmark=p.get("benchmark"),
            sample_size=p.get("sample_size", 0),
            reason=p.get("reason", ""),
        ),
        signals=[
            ScoredSignal(s["key"], s["label"], s["points"], s["reason"])
            for s in b.get("signals", [])
        ],
        strengths=list(b.get("strengths", [])),
        weaknesses=list(b.get("weaknesses", [])),
        missing_info=[
            MissingField(m["key"], m["label"]) for m in b.get("missing_info", [])
        ],
        recommendations=[
            Recommendation(r["action"], r["reason"], r["priority"])
            for r in b.get("recommendations", [])
        ],
        scorer=row.scorer,
    )


__all__ = [
    "MarketContext",
    "PropertyIntelligenceService",
    "extract_features",
]
