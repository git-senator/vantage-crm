"""LeadIntelligenceService — feature extraction, scoring, and the AI narrative.

The boundary between the two halves of lead intelligence runs through here. The
deterministic engine (`app/ai/lead_scoring.py`) is pure and knows nothing about
the database; this service is what reads a lead **under the caller's scope**,
turns it into features, runs the engine, persists the result, and — only when
asked, and only through `AIService` — layers a language narrative on top.

Every read goes through an existing scoped path:

  * the lead itself through `LeadService.get_lead`, which 404s a lead the caller
    cannot see, so scoring never touches a lead outside scope;
  * its engagement through `ActivityRepository.count_for_entity`, tenant-scoped;
  * prioritisation ranks stored scores joined to leads under the same
    `leads.view` scope the list endpoint resolves.

Two permission tiers, deliberately. The **deterministic** intelligence — score,
qualification, temperature, priority, risks, missing info, recommendations —
needs only `leads.view`: it is computed from CRM data with no egress and works
with the AI layer switched off entirely. The **generative narrative** needs
`ai.use` and runs through `AIService`, so the cost ceiling, the ledger and the
egress audit all apply.

The AI never writes to the lead. The score lives in `lead_scores`; the lead's own
fields are the agent's. That is the SECURITY.md §5 "no autonomous action" rule at
the level of a single feature.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

# Registers the lead-insight prompt in PROMPTS at import.
from app.ai import lead_prompts as _lead_prompts
from app.ai.context import instruction
from app.ai.lead_scoring import (
    DEFAULT_SCORER,
    LeadFeatures,
    LeadScore,
    LeadScorer,
    score_lead,
)
from app.ai.prompts import ContentBlock
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.lead import Lead
from app.models.user import User
from app.repositories.activity import ActivityRepository
from app.repositories.lead_score import LeadScoreRepository
from app.services.ai.context_builders import build_entity_context
from app.services.ai.service import AIService
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

FEATURE = "lead_insight"


def extract_features(lead: Lead, *, activity_count: int, now: datetime) -> LeadFeatures:
    """Turn a scoped lead into the engine's inputs. Pure given its arguments.

    Kept a free function so it can be unit-tested against a hand-built lead
    without a session, and so the mapping from CRM fields to features is one
    readable place rather than scattered through the service.
    """
    last = lead.last_contacted_at
    days_since_last = (now - last).days if last is not None else None
    created = lead.created_at or now
    return LeadFeatures(
        stage=lead.stage,
        status=lead.status,
        source=lead.source,
        agent_temperature=lead.temperature,
        has_email=bool(lead.email),
        has_phone=bool(lead.phone),
        has_budget=lead.budget_min is not None or lead.budget_max is not None,
        has_location=bool(lead.preferred_location),
        tag_count=len(lead.tags or []),
        activity_count=activity_count,
        days_since_last_contact=days_since_last,
        days_since_created=max(0, (now - created).days),
    )


def _breakdown(result: LeadScore) -> dict:  # type: ignore[type-arg]
    """The explanation as a JSON document for the `breakdown` column.

    Every signal, risk, missing field and recommendation with its reason — the
    score is reconstructible from this, which is the whole reason it is stored
    rather than trusted."""
    return {
        "signals": [
            {"key": s.key, "label": s.label, "points": s.points, "reason": s.reason}
            for s in result.signals
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


class LeadIntelligenceService:
    def __init__(
        self,
        session: AsyncSession,
        auth: AuthorizationContext,
        *,
        ai: AIService | None = None,
        scorer: LeadScorer | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings or get_settings()
        self.scorer = scorer or DEFAULT_SCORER
        self.activities = ActivityRepository(session)
        self.scores = LeadScoreRepository(session)
        self.rbac = RbacService(session)
        self._ai = ai

    @property
    def ai(self) -> AIService:
        if self._ai is None:
            self._ai = AIService(self.session, self.auth, settings=self.settings)
        return self._ai

    # ------------------------------------------------------- scoring

    async def score(self, lead_id: UUID, *, now: datetime | None = None) -> LeadScore:
        """Score one lead and persist the result. Requires `leads.view`.

        Deterministic: the same lead and context always produce the same score,
        because the engine is a pure function of the features extracted here.
        Persisting is an upsert, so a rescore replaces rather than accumulates.
        """
        from app.services.lead import LeadService

        self.auth.require("leads.view")
        moment = now or datetime.now(UTC)

        # Scoped fetch: 404s a lead the caller cannot see, so nothing outside
        # scope is ever scored.
        lead = await LeadService(self.session, self.auth).get_lead(lead_id)
        result = await self._score_lead(lead, moment)
        return result

    async def rescore(self, lead: Lead, *, now: datetime | None = None) -> LeadScore:
        """Score an already-fetched lead object. For the background sweep, which
        has read the leads under a system context and would only re-query by
        going back through `get_lead`. The caller owns the visibility guarantee —
        the sweep reads under RLS at ALL scope, deliberately."""
        return await self._score_lead(lead, now or datetime.now(UTC))

    async def _score_lead(self, lead: Lead, now: datetime) -> LeadScore:
        activity_count = await self.activities.count_for_entity(
            self.auth.organization_id, "lead", lead.id
        )
        features = extract_features(lead, activity_count=activity_count, now=now)
        result = score_lead(features, self.scorer)

        await self.scores.upsert(
            organization_id=self.auth.organization_id,
            lead_id=lead.id,
            score=result.score,
            temperature=result.temperature,
            qualification=result.qualification,
            priority=result.priority,
            buying_intent=result.buying_intent,
            breakdown=_breakdown(result),
            scorer=result.scorer,
        )
        return result

    async def prioritized(
        self, *, limit: int = 20
    ) -> list[tuple[Lead, LeadScore]]:
        """The caller's highest-scoring open leads, ranked.

        Reads stored scores joined to leads under the caller's `leads.view`
        scope, so the ranking shows only leads they could open in the list. A
        lead not yet scored simply does not appear — a background sweep and the
        score endpoint keep the table filled.
        """
        scope = self.auth.require("leads.view")
        owner_ids = await self.rbac.owner_ids_for_scope(self.auth, scope)

        rows = await self.scores.ranked(
            self.auth.organization_id, owner_ids=owner_ids, limit=limit
        )
        # The join in `ranked` already restricts to visible leads; re-fetch each
        # lead's row for its display fields. One query per row is acceptable at
        # this limit and keeps the repository returning scores, not join tuples.
        from app.services.lead import LeadService

        lead_service = LeadService(self.session, self.auth)
        out: list[tuple[Lead, LeadScore]] = []
        for row in rows:
            lead = await lead_service.get_lead(row.lead_id)
            out.append((lead, _reconstruct(row)))
        return out

    # --------------------------------------------------- ai narrative

    async def insights(
        self, lead_id: UUID, actor: User, *, now: datetime | None = None
    ) -> tuple[LeadScore, str]:
        """A deterministic score plus a grounded AI narrative. Requires `ai.use`.

        The score is computed and persisted first (the deterministic half). The
        narrative is then generated through `AIService` from the score's own
        signals plus the lead's fenced, redacted context — the model explains
        the number, it does not produce one.
        """
        from app.services.lead import LeadService

        self.auth.require("leads.view")
        self.auth.require("ai.use")
        moment = now or datetime.now(UTC)

        lead = await LeadService(self.session, self.auth).get_lead(lead_id)
        result = await self._score_lead(lead, moment)

        # Trusted content: the CRM's analysis, which we computed and which holds
        # only derived facts and generic reasons — no raw PII.
        blocks: list[ContentBlock] = [
            instruction("The CRM's analysis of this lead:"),
            instruction(_render_analysis(result)),
        ]
        # The lead's own fields arrive as fenced, redacted context, rebuilt under
        # scope through the same builder the assistant uses. Access is already
        # proven by the get_lead above, so this cannot leak.
        context = await build_entity_context(self.session, self.auth, "lead", lead_id)
        if context is not None:
            blocks.extend(context)

        request = _lead_prompts.LEAD_INSIGHT_PROMPT.build(
            blocks,
            model=self.settings.AI_MODEL,
            max_tokens=self.settings.AI_MAX_OUTPUT_TOKENS,
            metadata={"lead": str(lead_id)},
        )
        completion = await self.ai.complete(request, feature=FEATURE, actor=actor)
        return result, completion.text


def _render_analysis(result: LeadScore) -> str:
    """A compact, factual rendering of the score for the model to narrate.

    Trusted text: authored from our own computation, no raw customer PII. The
    model is told (in the prompt) to explain this, never to change the number.
    """
    lines = [
        f"Score: {result.score}/100 ({result.temperature}).",
        f"Qualification: {result.qualification}. Buying intent: {result.buying_intent}.",
        "Signals:",
    ]
    lines += [f"- {s.reason} ({s.points:+d})" for s in result.signals]
    if result.risks:
        lines.append("Risks:")
        lines += [f"- {r.label}: {r.detail}" for r in result.risks]
    if result.recommendations:
        lines.append("Recommended actions:")
        lines += [f"- {r.action}: {r.reason}" for r in result.recommendations]
    return "\n".join(lines)


def _reconstruct(row) -> LeadScore:  # type: ignore[no-untyped-def]
    """A stored row back into a `LeadScore`, from its breakdown JSON.

    The score is the sum of the stored signals, which is exactly why a round trip
    through the database loses nothing an ML model could not also produce."""
    from app.ai.lead_scoring import (
        MissingField,
        Recommendation,
        RiskFlag,
        ScoredSignal,
    )

    b = row.breakdown or {}
    return LeadScore(
        score=row.score,
        temperature=row.temperature,
        qualification=row.qualification,
        priority=row.priority,
        buying_intent=row.buying_intent,
        signals=[
            ScoredSignal(s["key"], s["label"], s["points"], s["reason"])
            for s in b.get("signals", [])
        ],
        risks=[
            RiskFlag(r["key"], r["label"], r["detail"]) for r in b.get("risks", [])
        ],
        missing_info=[
            MissingField(m["key"], m["label"]) for m in b.get("missing_info", [])
        ],
        recommendations=[
            Recommendation(r["action"], r["reason"], r["priority"])
            for r in b.get("recommendations", [])
        ],
        scorer=row.scorer,
    )


__all__ = ["FEATURE", "LeadIntelligenceService", "extract_features"]
