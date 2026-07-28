"""Trust posture aggregation — one rating from three existing signals.

The Trust Center does not compute a parallel notion of security or compliance
health: it folds the signals the other layers already produce — the security
dashboard's posture (Phase 8.2), the compliance checks' posture (Phase 8.3), and
this phase's risk register — into a single coarse trust rating a customer-facing
overview can show.

Pure and monotonic: a worse input never yields a better rating. The bands are
deliberately conservative — a mandatory compliance failure or an untreated
critical risk pins the rating to `at_risk` no matter how green everything else
is, because that is the honest thing to publish.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Coarse ratings, least to most reassuring.
TRUST_RATINGS: tuple[str, ...] = ("at_risk", "moderate", "strong")


@dataclass(frozen=True, slots=True)
class TrustSignals:
    """The three inputs the rating folds together, as primitives so the
    aggregation stays I/O-free."""

    #: SecurityDashboard.posture: "healthy" | "attention".
    security_posture: str
    #: Posture.status from the compliance checks: "compliant" | "warn" | "fail".
    compliance_posture: str
    open_critical_risks: int
    open_high_risks: int


def trust_rating(signals: TrustSignals) -> str:
    """Fold the signals into `at_risk` | `moderate` | `strong`.

    * `at_risk` — a mandatory compliance failure or any untreated critical risk.
      These are the conditions it would be dishonest to paper over.
    * `moderate` — a compliance warning, an open security concern, or an open
      high risk: managed, but not clean.
    * `strong` — compliant, healthy, and nothing high or critical outstanding.
    """
    if signals.compliance_posture == "fail" or signals.open_critical_risks > 0:
        return "at_risk"
    if (
        signals.compliance_posture == "warn"
        or signals.security_posture == "attention"
        or signals.open_high_risks > 0
    ):
        return "moderate"
    return "strong"


__all__ = [
    "TRUST_RATINGS",
    "TrustSignals",
    "trust_rating",
]
