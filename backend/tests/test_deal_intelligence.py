"""Deal intelligence — deterministic health, explainable win probability,
analytics-backed stalled detection, scope, isolation, narrative.

Phase 6.4. The properties that carry the milestone:

  * **Health is deterministic** and is the sum of its signals.
  * **Win probability is explainable** — the stage baseline plus stated ±
    factors, never a number from nowhere.
  * **Stalled detection reuses the Analytics Engine** — a deal is stalled
    against the pipeline's own mean-time-in-stage, not a hard-coded threshold.
  * **The AI never touches the deal** — health lives in `deal_scores`; the
    deal's `probability` is untouched.
  * **Scope and isolation hold** — an invisible deal cannot be scored, and
    at-risk ranks only visible deals.

The pure engine is tested without a database; the service tests use real deals
and stage transitions, and the echo provider for the narrative.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.deal_scoring import DealFeatures, score_deal
from app.core.config import Settings
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.deal_score import DealScore as DealScoreRow
from app.models.organization import Organization
from app.schemas.client import ClientCreate
from app.schemas.deal import DealCreate, DealStageTransition
from app.services.ai.deal_intelligence import DealIntelligenceService
from app.services.ai.echo import EchoCompletionProvider
from app.services.ai.service import AIService
from app.services.client import ClientService
from app.services.deal import DealService
from app.services.pipeline import build_default_pipeline
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

pytestmark = pytest.mark.integration

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _features(**overrides: object) -> DealFeatures:
    base: dict[str, object] = {
        "stage_name": "Qualification",
        "stage_position": 2,
        "is_won": False,
        "is_lost": False,
        "probability": 60,
        "priority": "high",
        "has_value": True,
        "value": Decimal("500000"),
        "has_property": True,
        "has_commission": True,
        "days_to_expected_close": 20,
        "days_in_current_stage": 5,
        "days_since_created": 30,
        "days_since_updated": 2,
        "pipeline_mean_days_in_stage": 12.0,
    }
    base.update(overrides)
    return DealFeatures(**base)  # type: ignore[arg-type]


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
        "deals.view": Scope.ALL,
        "deals.manage": Scope.ALL,
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


@pytest.fixture
async def pipeline(db: AsyncSession, organization: Organization):  # type: ignore[no-untyped-def]
    built = build_default_pipeline(organization.id)
    db.add(built)
    await db.flush()
    return built


def stage_by_key(pipeline, key: str):  # type: ignore[no-untyped-def]
    return next(stage for stage in pipeline.stages if stage.key == key)


# --------------------------------------------------- pure: the engine


class TestDealHealthEngine:
    def test_scoring_is_deterministic(self) -> None:
        f = _features()
        assert score_deal(f) == score_deal(f)

    def test_health_is_the_clamped_sum_of_signals(self) -> None:
        f = _features()
        result = score_deal(f)
        raw = sum(s.points for s in result.signals)
        assert result.health == min(100, max(0, raw))

    def test_win_probability_is_base_plus_stated_factors(self) -> None:
        """The heart of explainability: the number is the stage baseline plus the
        adjustments, each with a reason — never a guess."""
        f = _features(probability=60, days_in_current_stage=45, days_since_updated=30)
        result = score_deal(f)
        # 60 base, -15 stalled, -10 gone quiet.
        assert result.is_stalled
        assert result.win_probability == max(
            0, min(100, sum(x.points for x in result.probability_factors))
        )
        keys = {x.key for x in result.probability_factors}
        assert "base" in keys and "stalled" in keys and "quiet" in keys

    def test_stalled_uses_the_pipeline_norm_not_a_fixed_threshold(self) -> None:
        """A deal 20 days in a stage that *usually* takes 5 is stalled; the same
        20 days in a stage that usually takes 40 is not."""
        fast_norm = _features(days_in_current_stage=20, pipeline_mean_days_in_stage=5.0)
        slow_norm = _features(days_in_current_stage=20, pipeline_mean_days_in_stage=40.0)
        assert score_deal(fast_norm).is_stalled
        assert not score_deal(slow_norm).is_stalled

    def test_a_won_deal_is_maximally_healthy_a_lost_one_minimally(self) -> None:
        won = score_deal(_features(is_won=True))
        lost = score_deal(_features(is_lost=True))
        assert won.health == 100 and won.win_probability == 100 and won.status == "won"
        assert lost.health == 0 and lost.win_probability == 0 and lost.status == "lost"

    def test_forecast_value_is_value_times_win_probability(self) -> None:
        result = score_deal(
            _features(value=Decimal("400000"), probability=50, days_since_updated=2)
        )
        # win prob 50 (no penalties) → 400000 x 0.5 = 200000.
        assert result.win_probability == 50
        assert result.forecast_value == "200000.00"

    def test_overdue_and_missing_info_are_detected(self) -> None:
        result = score_deal(
            _features(days_to_expected_close=-10, has_value=False, has_property=False)
        )
        risk_keys = {r.key for r in result.risks}
        assert "overdue" in risk_keys and "no_value" in risk_keys
        missing = {m.key for m in result.missing_info}
        assert "value" in missing and "property" in missing

    def test_every_signal_and_recommendation_carries_a_reason(self) -> None:
        result = score_deal(_features(days_in_current_stage=60, days_since_updated=40))
        assert all(s.reason for s in result.signals)
        assert all(f.reason for f in result.probability_factors)
        assert all(r.reason for r in result.recommendations)


# --------------------------------------------------------- the service


class TestDealHealthService:
    async def test_health_persists_to_its_own_table_not_the_deal(
        self, db: AsyncSession, organization: Organization, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "deal@vantage.example")
        auth = _auth(organization, user.id)
        client = await ClientService(db, auth).create_client(
            ClientCreate(first_name="Omar", last_name="Haddad", type="seller"), user
        )
        deal = await DealService(db, auth).create_deal(
            DealCreate(title="1428 Sanchez", client_id=client.id, value=Decimal("900000")),
            user,
        )
        await db.flush()
        deal_probability_before = deal.probability

        result = await DealIntelligenceService(db, auth).score(deal.id)
        await db.flush()

        row = (
            await db.execute(select(DealScoreRow).where(DealScoreRow.deal_id == deal.id))
        ).scalar_one()
        assert row.health == result.health
        assert row.win_probability == result.win_probability
        assert row.breakdown["probability_factors"]
        # The deal's own probability was not touched by the AI.
        await db.refresh(deal)
        assert deal.probability == deal_probability_before

    async def test_stalled_detection_reads_analytics_stage_velocity(
        self, db: AsyncSession, organization: Organization, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        """End to end: a deal moved into a stage and left sitting is judged
        against the pipeline's own velocity, from the Analytics Engine."""
        user = await make_user(db, organization, "stall@vantage.example")
        auth = _auth(organization, user.id)
        client = await ClientService(db, auth).create_client(
            ClientCreate(first_name="C", last_name="One", type="buyer"), user
        )
        deal = await DealService(db, auth).create_deal(
            DealCreate(title="Stalled deal", client_id=client.id, value=Decimal("500000")),
            user,
        )
        await db.flush()

        # Backdate the opening stage-history row so the deal looks long-sitting.
        from datetime import UTC, datetime, timedelta

        from app.models.deal import DealStageHistory

        history = (
            await db.execute(
                select(DealStageHistory).where(DealStageHistory.deal_id == deal.id)
            )
        ).scalars().first()
        assert history is not None
        history.changed_at = datetime.now(UTC) - timedelta(days=60)
        await db.flush()

        result = await DealIntelligenceService(db, auth).score(deal.id)
        assert result.is_stalled
        assert any(r.key == "stalled" for r in result.risks)

    async def test_a_deal_outside_scope_cannot_be_scored(
        self, db: AsyncSession, organization: Organization, pipeline, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        owner = await make_user(db, organization, "owner@vantage.example")
        owner_auth = _auth(organization, owner.id)
        client = await ClientService(db, owner_auth).create_client(
            ClientCreate(first_name="P", last_name="Client", type="buyer"), owner
        )
        deal = await DealService(db, owner_auth).create_deal(
            DealCreate(title="Private deal", client_id=client.id), owner
        )
        await db.flush()

        other = await make_user(db, organization, "other@vantage.example")
        other_auth = AuthorizationContext(
            user_id=other.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"deals.view": Scope.OWN},
        )
        with pytest.raises(NotFoundError):
            await DealIntelligenceService(db, other_auth).score(deal.id)

    async def test_at_risk_ranks_visible_deals_worst_first(
        self, db: AsyncSession, organization: Organization, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "risk@vantage.example")
        auth = _auth(organization, user.id)
        service = DealIntelligenceService(db, auth)
        client = await ClientService(db, auth).create_client(
            ClientCreate(first_name="C", last_name="Two", type="buyer"), user
        )

        healthy = await DealService(db, auth).create_deal(
            DealCreate(
                title="Healthy",
                client_id=client.id,
                value=Decimal("900000"),
                priority="high",
            ),
            user,
        )
        await DealService(db, auth).move_stage(
            healthy.id,
            DealStageTransition(to_stage_id=stage_by_key(pipeline, "offer").id),
            user,
        )
        thin = await DealService(db, auth).create_deal(
            DealCreate(title="Thin", client_id=client.id), user
        )
        await db.flush()
        await service.score(healthy.id)
        await service.score(thin.id)
        await db.flush()

        ranked = await service.at_risk(limit=10)
        ids = [deal.id for deal, _ in ranked]
        # The thinner, lower-health deal ranks ahead of the healthier one.
        assert ids.index(thin.id) < ids.index(healthy.id)
        assert all(health.top_reasons for _, health in ranked)


# ------------------------------------------------------ the narrative


class TestNarrative:
    async def test_insights_score_and_narrate_through_the_guarded_ai(
        self, db: AsyncSession, organization: Organization, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "insight@vantage.example")
        auth = _auth(organization, user.id, **{"ai.use": Scope.ALL})
        client = await ClientService(db, auth).create_client(
            ClientCreate(first_name="Dana", last_name="Cole", type="buyer"), user
        )
        deal = await DealService(db, auth).create_deal(
            DealCreate(title="88 Townsend", client_id=client.id, value=Decimal("750000")),
            user,
        )
        await db.flush()

        provider = EchoCompletionProvider()
        ai = AIService(db, auth, provider=provider, settings=_settings())
        service = DealIntelligenceService(db, auth, ai=ai, settings=_settings())

        health, narrative = await service.insights(deal.id, user)
        assert health.health >= 0
        assert narrative.startswith("[echo]")

        from app.models.ai import AiJob

        job = (await db.execute(select(AiJob))).scalars().one()
        assert job.feature == "deal_insight"

        # The health read is in the prompt as trusted analysis, and the deal's
        # own context is fenced.
        sent = provider.requests[0]
        user_turn = sent.messages[-1].content
        assert "Health:" in user_turn and "Win probability:" in user_turn
        assert "<untrusted:" in user_turn

    async def test_insights_require_ai_use(
        self, db: AsyncSession, organization: Organization, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "noai@vantage.example")
        auth = _auth(organization, user.id)  # deals.view but not ai.use
        client = await ClientService(db, auth).create_client(
            ClientCreate(first_name="C", last_name="Three", type="buyer"), user
        )
        deal = await DealService(db, auth).create_deal(
            DealCreate(title="Deal", client_id=client.id), user
        )
        await db.flush()

        ai = AIService(db, auth, provider=EchoCompletionProvider(), settings=_settings())
        service = DealIntelligenceService(db, auth, ai=ai, settings=_settings())
        with pytest.raises(PermissionDeniedError):
            await service.insights(deal.id, user)


# ---------------------------------------------------- tenant isolation


class TestTenantIsolation:
    async def test_scores_do_not_cross_tenants(
        self, db: AsyncSession, organization: Organization, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        mine = await make_user(db, organization, "mine@vantage.example")
        theirs = await make_user(db, other_organization, "theirs@meridian.example")

        their_pipeline = build_default_pipeline(other_organization.id)
        db.add(their_pipeline)
        await db.flush()

        their_auth = _auth(other_organization, theirs.id)
        client = await ClientService(db, their_auth).create_client(
            ClientCreate(first_name="Foreign", last_name="Client", type="buyer"), theirs
        )
        deal = await DealService(db, their_auth).create_deal(
            DealCreate(title="Foreign deal", client_id=client.id, value=Decimal("500000")),
            theirs,
        )
        await db.flush()
        await DealIntelligenceService(db, their_auth).score(deal.id)
        await db.flush()

        ranked = await DealIntelligenceService(db, _auth(organization, mine.id)).at_risk()
        assert ranked == []


# -------------------------------------------------- shared primitives


class TestSharedExplainability:
    def test_lead_and_deal_scoring_share_the_same_primitives(self) -> None:
        """The explainability model is common, not duplicated per feature."""
        from app.ai.deal_scoring import ScoredSignal as DealSignal
        from app.ai.explain import ScoredSignal as Shared
        from app.ai.lead_scoring import ScoredSignal as LeadSignal

        assert Shared is DealSignal
        assert Shared is LeadSignal
