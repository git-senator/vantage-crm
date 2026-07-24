"""The deterministic lead-scoring engine.

This is the half of lead intelligence that must never be a black box, so it is
not one: a lead's score is the sum of a fixed set of signals, and every signal
carries the reason it contributed. There is no model here, no randomness, no
network — the same lead with the same context always scores the same, and the
number can always be read back as the reasons that produced it. That is what
"explainable" and "deterministic where possible" mean in practice.

The design is a **signal registry**, the same discipline as the metric, dataset
and prompt registries elsewhere. A signal is a pure function of `LeadFeatures`;
adding one is one entry and changes nothing else. And the whole engine sits
behind a `LeadScorer` protocol, so a future ML model can replace or augment the
rules while producing the same `LeadScore` shape — the service, the API and the
`lead_scores` table never learn which scorer ran.

Nothing in this module reads a database or an entity. It takes already-extracted
features and returns a result. Feature *extraction* — the part that must run
under the caller's scope — lives in the service; keeping the two apart is what
makes the scoring rules testable in isolation and the security boundary obvious.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

#: Score bounds. A lead is scored 0-100 so the number reads like a percentage of
#: "how promising", which is what an agent expects a lead score to mean.
MIN_SCORE = 0
MAX_SCORE = 100

#: The version stamped onto every score, so a change to these rules is visible in
#: the data — "scores shifted on the 3rd" has an answer. Bump on any material
#: change to a signal's points.
RULES_VERSION = "rules-v1"


@dataclass(frozen=True, slots=True)
class LeadFeatures:
    """The inputs the rules read. Pure data, extracted under scope elsewhere.

    Deliberately not the `Lead` model: the engine depends on a small, explicit
    feature set, not on the ORM, so a rule cannot accidentally reach into a
    relationship and trigger a query, and a test can construct a feature set by
    hand without a database.
    """

    stage: str
    status: str
    source: str
    #: The agent-set temperature (hot/warm/cold). A *human* signal the engine
    #: reads; distinct from the temperature the engine itself infers below.
    agent_temperature: str
    has_email: bool
    has_phone: bool
    has_budget: bool
    has_location: bool
    tag_count: int
    activity_count: int
    #: Days since the lead was last contacted, or `None` if never. `None` is not
    #: zero — "never contacted" and "contacted today" are opposite facts.
    days_since_last_contact: int | None
    days_since_created: int


@dataclass(frozen=True, slots=True)
class ScoredSignal:
    """One signal's contribution, with the reason it contributed."""

    key: str
    label: str
    points: int
    reason: str


@dataclass(frozen=True, slots=True)
class RiskFlag:
    key: str
    label: str
    detail: str


@dataclass(frozen=True, slots=True)
class MissingField:
    key: str
    label: str


@dataclass(frozen=True, slots=True)
class Recommendation:
    action: str
    reason: str
    #: high / medium / low — how much the next action wants attention now.
    priority: str


@dataclass(frozen=True, slots=True)
class LeadScore:
    """The full, explainable result. Every number here has its reasons attached."""

    score: int
    #: Inferred from the score band — the engine's own read, not the agent's.
    temperature: str
    qualification: str
    priority: str
    buying_intent: str
    signals: list[ScoredSignal] = field(default_factory=list)
    risks: list[RiskFlag] = field(default_factory=list)
    missing_info: list[MissingField] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    scorer: str = RULES_VERSION

    @property
    def top_reasons(self) -> list[str]:
        """The signals that moved the score most — the product's existing
        "three signals that drove it" promise, made literal."""
        ranked = sorted(self.signals, key=lambda s: abs(s.points), reverse=True)
        return [s.reason for s in ranked[:3]]


# --------------------------------------------------------------- signals


@dataclass(frozen=True, slots=True)
class Signal:
    """A named scoring rule. `evaluate` returns points + reason, or None to
    contribute nothing (and say nothing) for this lead."""

    key: str
    label: str
    evaluate: object  # Callable[[LeadFeatures], tuple[int, str] | None]


#: Source quality. Referrals and open-house leads convert best; a bare "other"
#: tells us least. Points are the engine's prior on each channel.
_SOURCE_POINTS = {
    "referral": (20, "Came from a referral, the strongest channel."),
    "open_house": (15, "Met at an open house."),
    "realtor_com": (11, "Came from realtor.com."),
    "zillow": (10, "Came from Zillow."),
    "website": (10, "Came from the website."),
    "instagram": (8, "Came from Instagram."),
    "cold_call": (5, "Sourced from a cold call."),
    "other": (3, "Source is not specified."),
}


def _source(f: LeadFeatures) -> tuple[int, str] | None:
    points, reason = _SOURCE_POINTS.get(f.source, _SOURCE_POINTS["other"])
    return points, reason


def _contact_method(f: LeadFeatures) -> tuple[int, str] | None:
    if f.has_email and f.has_phone:
        return 12, "Reachable by both email and phone."
    if f.has_email or f.has_phone:
        return 6, "Reachable by one channel."
    return 0, "No way to reach the lead yet."


def _budget(f: LeadFeatures) -> tuple[int, str] | None:
    if f.has_budget:
        return 15, "Budget is on record."
    return 0, "No budget captured yet."


def _location(f: LeadFeatures) -> tuple[int, str] | None:
    if f.has_location:
        return 5, "Preferred location is known."
    return None


_AGENT_TEMPERATURE_POINTS = {
    "hot": (18, "The agent marked this lead hot."),
    "warm": (9, "The agent marked this lead warm."),
    "cold": (2, "The agent marked this lead cold."),
}


def _agent_temperature(f: LeadFeatures) -> tuple[int, str] | None:
    return _AGENT_TEMPERATURE_POINTS.get(f.agent_temperature)


_STAGE_POINTS = {
    "new": (2, "Still a new lead."),
    "contacted": (8, "Has been contacted."),
    "qualified": (16, "Qualified in the pipeline."),
    "touring": (20, "Actively touring properties."),
    "unqualified": (-12, "Marked unqualified."),
}


def _stage(f: LeadFeatures) -> tuple[int, str] | None:
    return _STAGE_POINTS.get(f.stage)


def _engagement(f: LeadFeatures) -> tuple[int, str] | None:
    n = f.activity_count
    if n >= 6:
        return 15, f"Highly engaged — {n} interactions logged."
    if n >= 3:
        return 10, f"Engaged — {n} interactions logged."
    if n >= 1:
        return 5, f"Some engagement — {n} interaction(s) logged."
    return 0, "No interactions logged yet."


def _recency(f: LeadFeatures) -> tuple[int, str] | None:
    d = f.days_since_last_contact
    if d is None:
        return 0, "Has not been contacted yet."
    if d <= 2:
        return 12, "Contacted within the last two days."
    if d <= 7:
        return 8, "Contacted within the last week."
    if d <= 14:
        return 3, "Last contact was over a week ago."
    return -8, f"Going quiet — {d} days since last contact."


#: The registry. Order is display order in the breakdown; it does not affect the
#: total, which is a sum. Adding a signal is one entry here plus its function.
SIGNALS: list[Signal] = [
    Signal("source", "Source", _source),
    Signal("stage", "Pipeline stage", _stage),
    Signal("agent_temperature", "Agent temperature", _agent_temperature),
    Signal("budget", "Budget", _budget),
    Signal("contact_method", "Contactability", _contact_method),
    Signal("engagement", "Engagement", _engagement),
    Signal("recency", "Recency", _recency),
    Signal("location", "Preferred location", _location),
]


# -------------------------------------------------------- derived reads


def _temperature(score: int) -> str:
    """The engine's inferred temperature, from the score band."""
    if score >= 70:
        return "hot"
    if score >= 40:
        return "warm"
    return "cold"


def _qualification(f: LeadFeatures, score: int) -> str:
    if f.stage == "unqualified" or (not f.has_email and not f.has_phone):
        return "unqualified"
    if score >= 60 and (f.has_budget or f.stage in ("qualified", "touring")):
        return "qualified"
    return "nurture"


def _buying_intent(f: LeadFeatures) -> str:
    recent = f.days_since_last_contact is not None and f.days_since_last_contact <= 7
    advanced = f.stage in ("qualified", "touring")
    if advanced and f.has_budget and (recent or f.activity_count >= 3):
        return "strong"
    if advanced or (f.has_budget and f.activity_count >= 3):
        return "moderate"
    if f.activity_count >= 1 or f.has_budget:
        return "weak"
    return "none"


def _risks(f: LeadFeatures) -> list[RiskFlag]:
    risks: list[RiskFlag] = []
    d = f.days_since_last_contact
    # A hot lead going quiet is a sharper risk than a cold one.
    cold_threshold = 7 if f.agent_temperature == "hot" else 14
    if d is not None and d > cold_threshold and f.status == "open":
        risks.append(
            RiskFlag(
                "going_cold",
                "Going cold",
                f"{d} days since last contact.",
            )
        )
    if not f.has_email and not f.has_phone:
        risks.append(
            RiskFlag("no_contact_method", "No contact method", "No email or phone.")
        )
    if f.stage == "new" and f.days_since_created > 14 and f.status == "open":
        risks.append(
            RiskFlag(
                "stalled",
                "Stalled",
                f"Still 'new' after {f.days_since_created} days.",
            )
        )
    if f.stage == "unqualified":
        risks.append(
            RiskFlag("unqualified", "Unqualified", "Marked unqualified in the pipeline.")
        )
    return risks


def _missing_info(f: LeadFeatures) -> list[MissingField]:
    missing: list[MissingField] = []
    if not f.has_email:
        missing.append(MissingField("email", "Email address"))
    if not f.has_phone:
        missing.append(MissingField("phone", "Phone number"))
    if not f.has_budget:
        missing.append(MissingField("budget", "Budget"))
    if not f.has_location:
        missing.append(MissingField("preferred_location", "Preferred location"))
    if f.source == "other":
        missing.append(MissingField("source", "Lead source"))
    return missing


def _recommendations(
    f: LeadFeatures,
    risks: list[RiskFlag],
    buying_intent: str,
) -> list[Recommendation]:
    """Structured next actions, derived from the same facts as the score — so a
    recommendation is never a guess, it is a rule with a stated reason."""
    recs: list[Recommendation] = []
    risk_keys = {r.key for r in risks}

    if "going_cold" in risk_keys:
        recs.append(
            Recommendation(
                "Follow up now",
                "Contact has gone quiet and the lead is cooling.",
                "high",
            )
        )
    if "no_contact_method" in risk_keys:
        recs.append(
            Recommendation(
                "Add an email or phone number",
                "There is currently no way to reach this lead.",
                "high",
            )
        )
    if f.stage == "new" and (f.has_email or f.has_phone):
        recs.append(
            Recommendation(
                "Make first contact",
                "The lead is new and reachable but not yet contacted.",
                "high" if f.agent_temperature == "hot" else "medium",
            )
        )
    if not f.has_budget and f.stage in ("contacted", "qualified", "touring"):
        recs.append(
            Recommendation(
                "Capture the lead's budget",
                "Budget is missing on an engaged lead, which blocks matching.",
                "medium",
            )
        )
    if buying_intent == "strong" and f.stage == "qualified":
        recs.append(
            Recommendation(
                "Propose a showing",
                "Strong buying intent and qualified — ready to tour.",
                "high",
            )
        )
    if not f.has_location and f.stage in ("contacted", "qualified"):
        recs.append(
            Recommendation(
                "Ask about preferred locations",
                "Knowing where they want to buy sharpens matching.",
                "low",
            )
        )
    return recs


def _priority(score: int, risks: list[RiskFlag]) -> str:
    """How much the lead wants the agent's attention now. A promising lead at
    risk of cooling is the highest priority — more than a merely high score."""
    at_risk = any(r.key == "going_cold" for r in risks)
    if at_risk and score >= 40:
        return "high"
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


# --------------------------------------------------------------- engine


class LeadScorer(Protocol):
    """What every scorer implements — the seam a future ML model slots into.

    A model-based scorer would produce the same `LeadScore` (a number and its
    explanation) from the same `LeadFeatures`; because the service and the API
    depend on this protocol, swapping the implementation changes nothing they
    can see. That is the whole point of putting the engine behind an interface
    that returns explanations rather than a bare float.
    """

    version: str

    def score(self, features: LeadFeatures) -> LeadScore: ...


class RuleBasedScorer:
    """The deterministic engine. The default scorer, and the reference an ML
    scorer is measured against."""

    version = RULES_VERSION

    def score(self, features: LeadFeatures) -> LeadScore:
        signals: list[ScoredSignal] = []
        total = 0
        for signal in SIGNALS:
            outcome = signal.evaluate(features)  # type: ignore[operator]
            if outcome is None:
                continue
            points, reason = outcome
            total += points
            signals.append(
                ScoredSignal(signal.key, signal.label, points, reason)
            )

        score = max(MIN_SCORE, min(MAX_SCORE, total))
        risks = _risks(features)
        buying_intent = _buying_intent(features)

        return LeadScore(
            score=score,
            temperature=_temperature(score),
            qualification=_qualification(features, score),
            priority=_priority(score, risks),
            buying_intent=buying_intent,
            signals=signals,
            risks=risks,
            missing_info=_missing_info(features),
            recommendations=_recommendations(features, risks, buying_intent),
            scorer=self.version,
        )


#: The active scorer. A deployment swaps in an ML scorer by replacing this — the
#: only line that would change to move from rules to a model.
DEFAULT_SCORER: LeadScorer = RuleBasedScorer()


def score_lead(features: LeadFeatures, scorer: LeadScorer | None = None) -> LeadScore:
    """Score one lead's features. Deterministic for the rule-based scorer."""
    return (scorer or DEFAULT_SCORER).score(features)


__all__ = [
    "DEFAULT_SCORER",
    "MAX_SCORE",
    "MIN_SCORE",
    "RULES_VERSION",
    "SIGNALS",
    "LeadFeatures",
    "LeadScore",
    "LeadScorer",
    "MissingField",
    "Recommendation",
    "RiskFlag",
    "RuleBasedScorer",
    "ScoredSignal",
    "Signal",
    "score_lead",
]
