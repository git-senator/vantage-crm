"""Growth intelligence — deterministic business-health scoring, analytics reuse,
org-wide persistence, scope, isolation, briefing.

Phase 6.6. The properties that carry the milestone:

  * **The growth score is deterministic** and is the clamped sum of its signals.
  * **Every number comes from the Analytics Engine** — the service recomputes no
    metric; it reads `AnalyticsService.kpis` and the pipeline breakdown.
  * **The AI never writes CRM data** — the score lives in `growth_scores`, one
    canonical org-wide row, written only for an organization-wide computation.
  * **Scope is inherited** — a partial-scope caller is served a live read and
    does not overwrite the canonical row.
  * **Isolation holds** — one tenant's growth row is invisible to another.

The pure engine is tested without a database; the service tests use real leads
and deals, and the echo provider for the briefing.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.growth_scoring import (
    GrowthFeatures,
    StageSnapshot,
    score_growth,
)
from app.core.config import Settings
from app.core.exceptions import PermissionDeniedError
from app.core.permissions import Scope
from app.models.organization import Organization
from app.schemas.client import ClientCreate
from app.schemas.deal import DealCreate, DealStageTransition
from app.schemas.lead import LeadCreate
from app.services.ai.echo import EchoCompletionProvider
from app.services.ai.growth_intelligence import GrowthIntelligenceService
from app.services.ai.service import AIService
from app.services.client import ClientService
from app.services.deal import DealService
from app.services.lead import LeadService
from app.services.pipeline import build_default_pipeline
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

pytestmark = pytest.mark.integration

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _features(**overrides: object) -> GrowthFeatures:
    base: dict[str, object] = {
        "lead_conversion_rate": 20.0,
        "win_rate": 45.0,
        "sales_cycle_days": 40.0,
        "revenue_won": Decimal("500000"),
        "revenue_delta_pct": 12.0,
        "prior_revenue_won": Decimal("440000"),
        "deals_won": 5,
        "deals_lost": 3,
        "deals_created": 9,
        "leads_created": 20,
        "leads_converted": 4,
        "pipeline_open_value": Decimal("1200000"),
        "pipeline_weighted_value": Decimal("700000"),
        "average_deal_value": Decimal("100000"),
        "commission_earned": Decimal("15000"),
        "activities_logged": 40,
        "activities_delta_pct": 5.0,
        "tasks_completed": 30,
        "tasks_overdue": 2,
        "listings_active": 8,
        "listings_sold": 3,
        "pipeline_stages": (),
        "period_label": "this month",
    }
    base.update(overrides)
    return GrowthFeatures(**base)  # type: ignore[arg-type]


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
        "reports.view": Scope.ALL,
        "leads.view": Scope.ALL,
        "leads.manage": Scope.ALL,
        "deals.view": Scope.ALL,
        "deals.manage": Scope.ALL,
        "properties.view": Scope.ALL,
        "contacts.view": Scope.ALL,
        "contacts.manage": Scope.ALL,
        "tasks.view": Scope.ALL,
        "activities.view": Scope.ALL,
        **grants,
    }
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants=resolved,
    )


@pytest.fixture
async def pipeline(db: AsyncSession, organization: Organization):  # type: ignore[no-untyped-def]
    built = build_default_pipeline(organization.id)
    db.add(built)
    await db.flush()
    return built


def stage_by_key(pipeline, key: str):  # type: ignore[no-untyped-def]
    return next(stage for stage in pipeline.stages if stage.key == key)


# --------------------------------------------------- pure: the engine


class TestGrowthEngine:
    def test_scoring_is_deterministic(self) -> None:
        f = _features()
        assert score_growth(f) == score_growth(f)

    def test_score_is_the_clamped_sum_of_signals(self) -> None:
        f = _features()
        result = score_growth(f)
        raw = sum(s.points for s in result.signals)
        assert result.score == min(100, max(0, raw))

    def test_a_thriving_business_bands_high(self) -> None:
        result = score_growth(_features())
        assert result.score >= 55
        assert result.band in ("thriving", "steady")

    def test_a_struggling_business_bands_low_with_risks(self) -> None:
        struggling = _features(
            lead_conversion_rate=2.0,
            win_rate=10.0,
            sales_cycle_days=120.0,
            revenue_won=Decimal("50000"),
            revenue_delta_pct=-40.0,
            prior_revenue_won=Decimal("300000"),
            deals_won=1,
            deals_lost=6,
            deals_created=0,
            pipeline_weighted_value=Decimal("10000"),
            activities_logged=2,
            activities_delta_pct=-60.0,
            tasks_overdue=25,
        )
        result = score_growth(struggling)
        assert result.band in ("at_risk", "struggling")
        keys = {r.key for r in result.risks}
        assert "revenue_declining" in keys and "low_win_rate" in keys
        assert result.recommendations

    def test_revenue_signals_and_pipeline_insights_are_explainable(self) -> None:
        stages = (
            StageSnapshot("Qualification", 4, Decimal("200000"), 6.0),
            StageSnapshot("Offer", 3, Decimal("900000"), 34.0),
        )
        result = score_growth(_features(pipeline_stages=stages))
        assert any("Revenue" in s for s in result.revenue_signals)
        # Offer holds the most value and is the slowest → flagged the bottleneck.
        assert any("Offer" in i for i in result.pipeline_insights)
        assert any("bottleneck" in i for i in result.pipeline_insights)

    def test_every_signal_and_recommendation_carries_a_reason(self) -> None:
        result = score_growth(
            _features(revenue_delta_pct=-40.0, win_rate=10.0, tasks_overdue=25)
        )
        assert all(s.reason for s in result.signals)
        assert all(r.reason for r in result.recommendations)


# --------------------------------------------------------- the service


class TestGrowthService:
    async def _seed(self, db, auth, user, pipeline):  # type: ignore[no-untyped-def]
        client = await ClientService(db, auth).create_client(
            ClientCreate(first_name="Tomas", last_name="Vega", type="buyer"), user
        )
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Lena", last_name="Ito", email="lena@x.example"),
            user,
        )
        deal_service = DealService(db, auth)
        won = await deal_service.create_deal(
            DealCreate(title="Won", client_id=client.id, value=Decimal("400000")),
            user,
        )
        await deal_service.move_stage(
            won.id,
            DealStageTransition(to_stage_id=stage_by_key(pipeline, "closed_won").id),
            user,
        )
        await deal_service.create_deal(
            DealCreate(title="Open", client_id=client.id, value=Decimal("300000")),
            user,
        )
        await db.flush()

    async def test_org_wide_score_persists_one_row(
        self, db: AsyncSession, organization: Organization, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "growth@vantage.example")
        auth = _auth(organization, user.id)
        await self._seed(db, auth, user, pipeline)

        result = await GrowthIntelligenceService(db, auth).score()
        await db.flush()

        from app.repositories.growth_score import GrowthScoreRepository

        row = await GrowthScoreRepository(db).get_for_org(organization.id)
        assert row is not None
        assert row.score == result.score
        assert row.band == result.band
        assert row.breakdown["signals"]

    async def test_scoped_caller_does_not_write_the_canonical_row(
        self, db: AsyncSession, organization: Organization, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        """A caller who does not hold org-wide scope gets a live read but must not
        overwrite the canonical organization-wide row."""
        user = await make_user(db, organization, "scoped@vantage.example")
        # deals.view is OWN, so the computation is a partial slice, not org-wide.
        auth = _auth(organization, user.id, **{"deals.view": Scope.OWN})
        await self._seed(db, auth, user, pipeline)

        result = await GrowthIntelligenceService(db, auth).score()
        await db.flush()

        assert result.score >= 0  # a real read was returned
        from app.repositories.growth_score import GrowthScoreRepository

        row = await GrowthScoreRepository(db).get_for_org(organization.id)
        assert row is None  # but nothing was persisted

    async def test_score_requires_reports_view(
        self, db: AsyncSession, organization: Organization, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "noreports@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"leads.view": Scope.OWN},  # no reports.view
        )
        with pytest.raises(PermissionDeniedError):
            await GrowthIntelligenceService(db, auth).score()


# ------------------------------------------------------ the briefing


class TestBriefing:
    async def test_briefing_scores_and_narrates_through_the_guarded_ai(
        self, db: AsyncSession, organization: Organization, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "brief@vantage.example")
        auth = _auth(organization, user.id, **{"ai.use": Scope.ALL})

        provider = EchoCompletionProvider()
        ai = AIService(db, auth, provider=provider, settings=_settings())
        service = GrowthIntelligenceService(db, auth, ai=ai, settings=_settings())

        result, narrative = await service.briefing(user)
        assert result.score >= 0
        assert narrative.startswith("[echo]")

        from sqlalchemy import select

        from app.models.ai import AiJob

        job = (await db.execute(select(AiJob))).scalars().one()
        assert job.feature == "growth_briefing"

        # The growth read is in the prompt as trusted analysis. There is no raw
        # per-record context, so nothing is fenced.
        sent = provider.requests[0]
        user_turn = sent.messages[-1].content
        assert "Growth score:" in user_turn

    async def test_briefing_requires_ai_use(
        self, db: AsyncSession, organization: Organization, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "noai@vantage.example")
        auth = _auth(organization, user.id)  # reports.view but not ai.use

        ai = AIService(db, auth, provider=EchoCompletionProvider(), settings=_settings())
        service = GrowthIntelligenceService(db, auth, ai=ai, settings=_settings())
        with pytest.raises(PermissionDeniedError):
            await service.briefing(user)


# ---------------------------------------------------- tenant isolation


class TestTenantIsolation:
    async def test_growth_rows_do_not_cross_tenants(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:  # type: ignore[no-untyped-def]
        theirs = await make_user(db, other_organization, "theirs@meridian.example")
        their_pipeline = build_default_pipeline(other_organization.id)
        db.add(their_pipeline)
        await db.flush()

        their_auth = _auth(other_organization, theirs.id)
        await GrowthIntelligenceService(db, their_auth).score()
        await db.flush()

        from app.repositories.growth_score import GrowthScoreRepository

        # The other tenant's canonical row exists; this tenant's does not.
        assert await GrowthScoreRepository(db).get_for_org(other_organization.id) is not None
        assert await GrowthScoreRepository(db).get_for_org(organization.id) is None


# -------------------------------------------------- shared primitives


class TestSharedExplainability:
    def test_growth_scoring_shares_the_common_primitives(self) -> None:
        from app.ai.explain import RiskFlag as Shared
        from app.ai.explain import ScoredSignal as SharedSignal
        from app.ai.growth_scoring import RiskFlag as GrowthRisk
        from app.ai.growth_scoring import ScoredSignal as GrowthSignal

        assert Shared is GrowthRisk
        assert SharedSignal is GrowthSignal
