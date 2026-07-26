"""Property intelligence — deterministic listing quality, explainable pricing,
completeness, analytics-backed comps, scope, isolation, generated content.

Phase 6.5. The properties that carry the milestone:

  * **Quality is deterministic** and is the sum of its signals.
  * **Completeness is a separate, flat reading** of the listing checklist.
  * **Pricing is explainable** — a stance against the market's comparable median
    from the Analytics Engine, never a number from nowhere.
  * **Strengths and weaknesses read off the signals** — the same facts, split by
    sign, not a second opinion.
  * **The AI never touches the listing** — quality lives in `property_scores`;
    the listing's own fields are the agent's.
  * **Isolation holds** — a listing in another tenant cannot be scored, and
    needs-attention ranks only this tenant's listings.

The pure engine is tested without a database; the service tests use real
listings, real market comps, and the echo provider for the generated content.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.property_scoring import PropertyFeatures, score_property
from app.core.config import Settings
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.organization import Organization
from app.models.property_score import PropertyScore as PropertyScoreRow
from app.schemas.property import PropertyCreate
from app.services.ai.echo import EchoCompletionProvider
from app.services.ai.property_intelligence import PropertyIntelligenceService
from app.services.ai.service import AIService
from app.services.property import PropertyService
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

pytestmark = pytest.mark.integration

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _features(**overrides: object) -> PropertyFeatures:
    base: dict[str, object] = {
        "property_type": "condo",
        "status": "active",
        "description_length": 500,
        "feature_count": 5,
        "has_price": True,
        "price": Decimal("500000"),
        "has_bedrooms": True,
        "has_bathrooms": True,
        "has_square_feet": True,
        "square_feet": 1000,
        "has_year_built": True,
        "has_lot_size": True,
        "has_geo": True,
        "has_mls": True,
        "days_on_market": 10,
        "comp_median_price_per_sqft": None,
        "comp_sample_size": 0,
        "market_avg_days_on_market": None,
    }
    base.update(overrides)
    return PropertyFeatures(**base)  # type: ignore[arg-type]


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "JWT_SECRET": SecretStr(TEST_JWT_SECRET),
        "ENVIRONMENT": "test",
        "AI_ENABLED": True,
        "AI_PROVIDER": "echo",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _auth(organization: Organization, user_id, **grants: Scope) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    resolved = {
        "properties.view": Scope.ALL,
        "properties.manage": Scope.ALL,
        "contacts.view": Scope.ALL,
        "contacts.manage": Scope.ALL,
        **grants,
    }
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants=resolved,
    )


def _listing(**overrides: object) -> PropertyCreate:
    base: dict[str, object] = {
        "title": "1428 Sanchez",
        "address_line1": "1428 Sanchez St",
        "city": "San Francisco",
        "state": "CA",
        "postal_code": "94131",
        "property_type": "condo",
    }
    base.update(overrides)
    return PropertyCreate(**base)  # type: ignore[arg-type]


# --------------------------------------------------- pure: the engine


class TestPropertyQualityEngine:
    def test_scoring_is_deterministic(self) -> None:
        f = _features()
        assert score_property(f) == score_property(f)

    def test_quality_is_the_clamped_sum_of_signals(self) -> None:
        f = _features()
        result = score_property(f)
        raw = sum(s.points for s in result.signals)
        assert result.quality == min(100, max(0, raw))

    def test_a_complete_listing_is_100_percent_complete(self) -> None:
        assert score_property(_features()).completeness == 100

    def test_an_empty_listing_scores_low_and_incomplete(self) -> None:
        bare = _features(
            description_length=0,
            feature_count=0,
            has_price=False,
            price=None,
            has_bedrooms=False,
            has_bathrooms=False,
            has_square_feet=False,
            square_feet=None,
            has_year_built=False,
            has_lot_size=False,
            has_geo=False,
            has_mls=False,
        )
        result = score_property(bare)
        assert result.completeness == 0
        assert result.grade == "poor"
        missing = {m.key for m in result.missing_info}
        assert {"description", "price", "square_feet", "features"} <= missing

    def test_pricing_is_an_explainable_stance_against_the_comp_median(self) -> None:
        """The heart of the pricing insight: a stance measured against a named
        market benchmark, never a bare assertion."""
        over = _features(
            price=Decimal("400000"),
            square_feet=1000,  # $400/sqft
            comp_median_price_per_sqft=Decimal("200"),
            comp_sample_size=12,
        )
        result = score_property(over)
        assert result.pricing.stance == "above"
        assert result.pricing.delta_pct == 100
        assert result.pricing.benchmark is not None
        assert "median" in result.pricing.reason

    def test_pricing_is_unknown_without_comps(self) -> None:
        result = score_property(_features(comp_median_price_per_sqft=None))
        assert result.pricing.stance == "unknown"
        assert result.pricing.delta_pct is None

    def test_strengths_and_weaknesses_read_off_the_signals(self) -> None:
        f = _features(description_length=0)  # a clear weakness
        result = score_property(f)
        assert result.strengths  # a well-filled listing has some
        assert any("description" in w.lower() for w in result.weaknesses)

    def test_land_is_judged_on_lot_size_not_bedrooms(self) -> None:
        land = _features(
            property_type="land",
            has_bedrooms=False,
            has_bathrooms=False,
            has_square_feet=False,
            square_feet=None,
            has_year_built=False,
            has_lot_size=True,
        )
        result = score_property(land)
        # No bedroom/bathroom missing-info for land, but lot size counts.
        missing = {m.key for m in result.missing_info}
        assert "bedrooms" not in missing and "bathrooms" not in missing
        assert any(s.key == "core_specs" for s in result.signals)

    def test_every_signal_and_recommendation_carries_a_reason(self) -> None:
        result = score_property(
            _features(description_length=0, has_price=False, price=None)
        )
        assert all(s.reason for s in result.signals)
        assert all(r.reason for r in result.recommendations)


# --------------------------------------------------------- the service


class TestPropertyQualityService:
    async def test_quality_persists_to_its_own_table_not_the_property(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "prop@vantage.example")
        auth = _auth(organization, user.id)
        listing = await PropertyService(db, auth).create_property(
            _listing(price=Decimal("750000"), description="A fine home."), user
        )
        await db.flush()
        title_before, price_before = listing.title, listing.price

        result = await PropertyIntelligenceService(db, auth).score(listing.id)
        await db.flush()

        row = (
            await db.execute(
                select(PropertyScoreRow).where(
                    PropertyScoreRow.property_id == listing.id
                )
            )
        ).scalar_one()
        assert row.quality == result.quality
        assert row.completeness == result.completeness
        assert row.breakdown["signals"]
        # The listing's own fields were not touched by the AI.
        await db.refresh(listing)
        assert listing.title == title_before and listing.price == price_before

    async def test_pricing_reuses_the_analytics_comp_median(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """End to end: an asking price is judged against the market's comparable
        median, computed by the Analytics Engine over the tenant's listings."""
        user = await make_user(db, organization, "comps@vantage.example")
        auth = _auth(organization, user.id)
        service = PropertyService(db, auth)

        # Three comparable condos at ~$200/sqft set the market median.
        for i in range(3):
            await service.create_property(
                _listing(
                    title=f"Comp {i}",
                    price=Decimal("200000"),
                    square_feet=1000,
                ),
                user,
            )
        # The subject condo is priced at $400/sqft — double the comps.
        subject = await service.create_property(
            _listing(title="Subject", price=Decimal("400000"), square_feet=1000),
            user,
        )
        await db.flush()

        result = await PropertyIntelligenceService(db, auth).score(subject.id)
        assert result.pricing.stance == "above"
        assert result.pricing.sample_size >= 4
        assert any(r.action == "Review the asking price" for r in result.recommendations)

    async def test_needs_attention_ranks_worst_first(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "attn@vantage.example")
        auth = _auth(organization, user.id)
        service = PropertyIntelligenceService(db, auth)
        props = PropertyService(db, auth)

        rich = await props.create_property(
            _listing(
                title="Rich",
                description="A" * 500,
                features=["pool", "garage", "view", "solar"],
                price=Decimal("900000"),
                bedrooms=3,
                bathrooms=Decimal("2.5"),
                square_feet=2000,
                year_built=2015,
                latitude=Decimal("37.75"),
                longitude=Decimal("-122.43"),
                mls_number="MLS-1",
            ),
            user,
        )
        thin = await props.create_property(_listing(title="Thin"), user)
        await db.flush()
        await service.score(rich.id)
        await service.score(thin.id)
        await db.flush()

        ranked = await service.needs_attention(limit=10)
        ids = [listing.id for listing, _ in ranked]
        assert ids.index(thin.id) < ids.index(rich.id)
        assert all(quality.top_reasons for _, quality in ranked)

    async def test_a_listing_in_another_tenant_cannot_be_scored(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        theirs = await make_user(db, other_organization, "theirs@meridian.example")
        their_auth = _auth(other_organization, theirs.id)
        foreign = await PropertyService(db, their_auth).create_property(
            _listing(title="Foreign listing"), theirs
        )
        await db.flush()

        mine = await make_user(db, organization, "mine@vantage.example")
        with pytest.raises(NotFoundError):
            await PropertyIntelligenceService(db, _auth(organization, mine.id)).score(
                foreign.id
            )


# ------------------------------------------------------ the content


class TestGeneratedContent:
    async def test_content_scores_and_generates_through_the_guarded_ai(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "content@vantage.example")
        auth = _auth(organization, user.id, **{"ai.use": Scope.ALL})
        listing = await PropertyService(db, auth).create_property(
            _listing(price=Decimal("650000"), description="A bright condo."), user
        )
        await db.flush()

        provider = EchoCompletionProvider()
        ai = AIService(db, auth, provider=provider, settings=_settings())
        service = PropertyIntelligenceService(db, auth, ai=ai, settings=_settings())

        quality, content = await service.generate(listing.id, "description", user)
        assert quality.quality >= 0
        assert content.startswith("[echo]")

        from app.models.ai import AiJob

        job = (await db.execute(select(AiJob))).scalars().one()
        assert job.feature == "property_description"

        # The quality read is in the prompt as trusted analysis, and the
        # listing's own context is fenced.
        sent = provider.requests[0]
        user_turn = sent.messages[-1].content
        assert "Listing quality:" in user_turn
        assert "<untrusted:" in user_turn

    async def test_content_requires_ai_use(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "noai@vantage.example")
        auth = _auth(organization, user.id)  # properties.view but not ai.use
        listing = await PropertyService(db, auth).create_property(
            _listing(title="Listing"), user
        )
        await db.flush()

        ai = AIService(db, auth, provider=EchoCompletionProvider(), settings=_settings())
        service = PropertyIntelligenceService(db, auth, ai=ai, settings=_settings())
        with pytest.raises(PermissionDeniedError):
            await service.generate(listing.id, "summary", user)


# ---------------------------------------------------- tenant isolation


class TestTenantIsolation:
    async def test_scores_do_not_cross_tenants(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        mine = await make_user(db, organization, "a@vantage.example")
        theirs = await make_user(db, other_organization, "b@meridian.example")

        their_auth = _auth(other_organization, theirs.id)
        listing = await PropertyService(db, their_auth).create_property(
            _listing(title="Foreign", price=Decimal("500000")), theirs
        )
        await db.flush()
        await PropertyIntelligenceService(db, their_auth).score(listing.id)
        await db.flush()

        ranked = await PropertyIntelligenceService(
            db, _auth(organization, mine.id)
        ).needs_attention()
        assert ranked == []


# -------------------------------------------------- shared primitives


class TestSharedExplainability:
    def test_property_scoring_shares_the_common_primitives(self) -> None:
        """The explainability model is common, not duplicated per feature."""
        from app.ai.explain import MissingField as Shared
        from app.ai.explain import ScoredSignal as SharedSignal
        from app.ai.property_scoring import MissingField as PropMissing
        from app.ai.property_scoring import ScoredSignal as PropSignal

        assert Shared is PropMissing
        assert SharedSignal is PropSignal
