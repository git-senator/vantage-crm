"""Lead intelligence — deterministic scoring, explainability, scope, narrative.

Phase 6.3. The properties that carry the milestone:

  * **Scoring is deterministic** — the same features always produce the same
    score, and the score is exactly the (clamped) sum of its signals.
  * **Every score is explainable** — signals, risks, missing info and
    recommendations, each with a reason.
  * **The AI never touches the lead** — the score lives in `lead_scores`; the
    lead's own fields are untouched.
  * **Scoring obeys scope** — a lead the caller cannot see cannot be scored, and
    prioritisation ranks only visible leads.
  * **The narrative is grounded** — it goes through the guarded AIService, over
    the score the rules produced, with the lead's context fenced and redacted.

The pure engine is tested without a database; the service and isolation tests use
the echo provider so the narrative path runs with no key and no network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.lead_scoring import (
    MAX_SCORE,
    RULES_VERSION,
    LeadFeatures,
    score_lead,
)
from app.core.config import Settings
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.lead import Lead
from app.models.lead_score import LeadScore as LeadScoreRow
from app.models.organization import Organization
from app.schemas.lead import LeadCreate
from app.services.ai.echo import EchoCompletionProvider
from app.services.ai.lead_intelligence import LeadIntelligenceService, extract_features
from app.services.ai.service import AIService
from app.services.lead import LeadService
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

pytestmark = pytest.mark.integration

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _features(**overrides: object) -> LeadFeatures:
    base: dict[str, object] = {
        "stage": "qualified",
        "status": "open",
        "source": "referral",
        "agent_temperature": "hot",
        "has_email": True,
        "has_phone": True,
        "has_budget": True,
        "has_location": True,
        "tag_count": 2,
        "activity_count": 5,
        "days_since_last_contact": 1,
        "days_since_created": 10,
    }
    base.update(overrides)
    return LeadFeatures(**base)  # type: ignore[arg-type]


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
    resolved = {"leads.view": Scope.ALL, "leads.manage": Scope.ALL, **grants}
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants=resolved,
    )


# ------------------------------------------------------ pure: the engine


class TestScoringEngine:
    def test_scoring_is_deterministic(self) -> None:
        f = _features()
        assert score_lead(f) == score_lead(f)

    def test_the_score_is_the_clamped_sum_of_its_signals(self) -> None:
        """Explainability by construction: the number is nothing more than the
        signals, so it can always be read back as them."""
        f = _features()
        result = score_lead(f)
        raw = sum(s.points for s in result.signals)
        assert result.score == min(MAX_SCORE, max(0, raw))

    def test_a_strong_lead_scores_high_and_a_thin_one_low(self) -> None:
        strong = score_lead(_features())
        thin = score_lead(
            _features(
                stage="new",
                source="other",
                agent_temperature="cold",
                has_email=False,
                has_phone=False,
                has_budget=False,
                has_location=False,
                activity_count=0,
                days_since_last_contact=None,
                days_since_created=40,
            )
        )
        assert strong.score > thin.score
        assert strong.temperature == "hot"
        assert thin.temperature == "cold"

    def test_missing_info_is_detected_with_labels(self) -> None:
        result = score_lead(
            _features(has_email=False, has_phone=False, has_budget=False, source="other")
        )
        keys = {m.key for m in result.missing_info}
        assert {"email", "phone", "budget", "source"} <= keys

    def test_a_lead_with_no_contact_method_is_unqualified(self) -> None:
        result = score_lead(_features(has_email=False, has_phone=False))
        assert result.qualification == "unqualified"
        assert any(r.key == "no_contact_method" for r in result.risks)

    def test_going_cold_is_a_risk_and_drives_a_high_priority_action(self) -> None:
        """A promising lead going quiet is the sharpest signal a CRM has."""
        result = score_lead(_features(days_since_last_contact=30))
        assert any(r.key == "going_cold" for r in result.risks)
        assert any(
            rec.action == "Follow up now" and rec.priority == "high"
            for rec in result.recommendations
        )

    def test_strong_buying_intent_on_a_qualified_lead(self) -> None:
        result = score_lead(
            _features(stage="qualified", has_budget=True, activity_count=4)
        )
        assert result.buying_intent == "strong"

    def test_every_signal_and_recommendation_carries_a_reason(self) -> None:
        result = score_lead(_features(stage="new", days_since_last_contact=30))
        assert all(s.reason for s in result.signals)
        assert all(rec.reason for rec in result.recommendations)
        assert result.scorer == RULES_VERSION

    def test_top_reasons_are_the_biggest_movers(self) -> None:
        result = score_lead(_features())
        assert len(result.top_reasons) <= 3
        assert result.top_reasons[0] in {s.reason for s in result.signals}

    def test_never_contacted_is_not_treated_as_recent(self) -> None:
        never = score_lead(_features(days_since_last_contact=None))
        recent = score_lead(_features(days_since_last_contact=1))
        assert never.score < recent.score


# ------------------------------------------------------ feature mapping


class TestFeatureExtraction:
    def test_lead_fields_map_to_features(self) -> None:
        now = datetime(2026, 7, 1, tzinfo=UTC)
        lead = Lead(
            first_name="A",
            last_name="B",
            stage="qualified",
            status="open",
            source="referral",
            temperature="hot",
            email="a@b.com",
            phone=None,
            budget_min=Decimal("500000"),
            preferred_location="SF",
            tags=["investor"],
            last_contacted_at=now - timedelta(days=3),
            created_at=now - timedelta(days=20),
        )
        features = extract_features(lead, activity_count=4, now=now)
        assert features.has_email and not features.has_phone
        assert features.has_budget and features.has_location
        assert features.days_since_last_contact == 3
        assert features.days_since_created == 20
        assert features.activity_count == 4


# --------------------------------------------------------- the service


class TestScoringService:
    async def test_scoring_persists_to_its_own_table_not_the_lead(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """The AI must never modify CRM data: the lead's own score field is
        untouched, the AI's read lives in lead_scores."""
        user = await make_user(db, organization, "score@vantage.example")
        auth = _auth(organization, user.id)
        lead = await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Yuki", last_name="Tanaka", source="referral"), user
        )
        await db.flush()
        lead_score_before = lead.score

        result = await LeadIntelligenceService(db, auth).score(lead.id)
        await db.flush()

        # A row was written to lead_scores...
        row = (
            await db.execute(
                select(LeadScoreRow).where(LeadScoreRow.lead_id == lead.id)
            )
        ).scalar_one()
        assert row.score == result.score
        assert row.breakdown["signals"]
        # ...and the lead's own field was not touched by the AI.
        await db.refresh(lead)
        assert lead.score == lead_score_before

    async def test_rescoring_upserts_rather_than_duplicating(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "upsert@vantage.example")
        auth = _auth(organization, user.id)
        lead = await LeadService(db, auth).create_lead(
            LeadCreate(first_name="A", last_name="B"), user
        )
        await db.flush()

        service = LeadIntelligenceService(db, auth)
        await service.score(lead.id)
        await service.score(lead.id)
        await db.flush()

        from sqlalchemy import func

        count = (
            await db.execute(
                select(func.count())
                .select_from(LeadScoreRow)
                .where(LeadScoreRow.lead_id == lead.id)
            )
        ).scalar()
        assert count == 1

    async def test_a_lead_outside_scope_cannot_be_scored(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        owner = await make_user(db, organization, "owner@vantage.example")
        owner_auth = AuthorizationContext(
            user_id=owner.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"leads.view": Scope.OWN, "leads.manage": Scope.OWN},
        )
        lead = await LeadService(db, owner_auth).create_lead(
            LeadCreate(first_name="Private", last_name="Lead"), owner
        )
        await db.flush()

        other = await make_user(db, organization, "other@vantage.example")
        other_auth = AuthorizationContext(
            user_id=other.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"leads.view": Scope.OWN},
        )
        with pytest.raises(NotFoundError):
            await LeadIntelligenceService(db, other_auth).score(lead.id)

    async def test_scoring_requires_leads_view(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        import uuid

        user = await make_user(db, organization, "nogrant@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={},
        )
        with pytest.raises(PermissionDeniedError):
            await LeadIntelligenceService(db, auth).score(uuid.uuid4())

    async def test_prioritized_ranks_visible_leads_by_score(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "prio@vantage.example")
        auth = _auth(organization, user.id)
        service = LeadIntelligenceService(db, auth)

        strong = await LeadService(db, auth).create_lead(
            LeadCreate(
                first_name="Strong",
                last_name="Lead",
                source="referral",
                temperature="hot",
                stage="qualified",
                email="s@x.com",
                phone="+14155550100",
                budget_min=Decimal("900000"),
            ),
            user,
        )
        weak = await LeadService(db, auth).create_lead(
            LeadCreate(
                first_name="Weak", last_name="Lead", source="other", temperature="cold"
            ),
            user,
        )
        await db.flush()
        await service.score(strong.id)
        await service.score(weak.id)
        await db.flush()

        ranked = await service.prioritized(limit=10)
        ids = [lead.id for lead, _ in ranked]
        assert ids.index(strong.id) < ids.index(weak.id)
        # Each ranked lead carries its explanation.
        assert all(score.top_reasons for _, score in ranked)


# ------------------------------------------------------ the narrative


class TestNarrative:
    async def test_insights_score_deterministically_and_narrate_through_ai(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "insight@vantage.example")
        auth = _auth(organization, user.id, **{"ai.use": Scope.ALL})
        lead = await LeadService(db, auth).create_lead(
            LeadCreate(
                first_name="Dana",
                last_name="Cole",
                source="referral",
                notes="Interested in a duplex. Call at dana@example.com.",
            ),
            user,
        )
        await db.flush()

        provider = EchoCompletionProvider()
        ai = AIService(db, auth, provider=provider, settings=_settings())
        service = LeadIntelligenceService(db, auth, ai=ai, settings=_settings())

        score, narrative = await service.insights(lead.id, user)
        assert score.score > 0
        assert narrative.startswith("[echo]")

        # The narrative went through the guarded path: a ledger row exists.
        from app.models.ai import AiJob

        job = (await db.execute(select(AiJob))).scalars().one()
        assert job.feature == "lead_insight"

        # The lead's context reached the model fenced and redacted.
        sent = provider.requests[0]
        user_turn = sent.messages[-1].content
        assert "<untrusted:notes>" in user_turn
        assert "[email]" in user_turn
        assert "dana@example.com" not in user_turn
        # The score the rules produced is in the prompt as trusted analysis.
        assert "Score:" in user_turn

    async def test_insights_require_ai_use(
        self, db: AsyncSession, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "noai@vantage.example")
        # leads.view but not ai.use.
        auth = _auth(organization, user.id)
        lead = await LeadService(db, auth).create_lead(
            LeadCreate(first_name="A", last_name="B"), user
        )
        await db.flush()

        ai = AIService(db, auth, provider=EchoCompletionProvider(), settings=_settings())
        service = LeadIntelligenceService(db, auth, ai=ai, settings=_settings())
        with pytest.raises(PermissionDeniedError):
            await service.insights(lead.id, user)


# ---------------------------------------------------- tenant isolation


class TestTenantIsolation:
    async def test_scores_do_not_cross_tenants(
        self, db: AsyncSession, organization: Organization, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        mine = await make_user(db, organization, "mine@vantage.example")
        theirs = await make_user(db, other_organization, "theirs@meridian.example")

        their_auth = _auth(other_organization, theirs.id)
        their_lead = await LeadService(db, their_auth).create_lead(
            LeadCreate(first_name="Foreign", last_name="Lead", source="referral"),
            theirs,
        )
        await db.flush()
        await LeadIntelligenceService(db, their_auth).score(their_lead.id)
        await db.flush()

        # The first org's prioritised list sees none of the second org's scores.
        ranked = await LeadIntelligenceService(db, _auth(organization, mine.id)).prioritized()
        assert ranked == []
