"""The certified-integration framework.

A marketplace needs a way to say how much a listing is trusted, and to derive it
from evidence rather than a hand-set flag. Certification is an ordered ladder:

  * ``community`` — listed, nothing more asserted.
  * ``verified`` — a known publisher and a manifest that validates.
  * ``certified`` — the above, plus its capabilities and security posture were
    reviewed.
  * ``official`` — a first-party listing the platform ships and stands behind.

The tiers are ordered so a caller can ask "is this at least certified?" without
hard-coding the set, and :func:`assess_certification` computes a tier from
signals so the same evidence always yields the same tier — the certification is
explainable, not editorial.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The certification ladder, lowest trust first. Index is the rank.
CERTIFICATION_TIERS: tuple[str, ...] = ("community", "verified", "certified", "official")

_RANK: dict[str, int] = {tier: index for index, tier in enumerate(CERTIFICATION_TIERS)}

#: A listing is "certified" for trust purposes at this tier or above.
CERTIFIED_FLOOR = "certified"


def certification_rank(tier: str) -> int:
    """The numeric rank of a tier. Raises ``KeyError`` for an unknown one."""
    try:
        return _RANK[tier]
    except KeyError as exc:
        raise KeyError(f"Unknown certification tier '{tier}'.") from exc


def is_certified(tier: str) -> bool:
    """Whether a tier clears the certified floor (certified or official)."""
    return certification_rank(tier) >= _RANK[CERTIFIED_FLOOR]


@dataclass(frozen=True, slots=True)
class CertificationSignals:
    """The evidence a tier is derived from."""

    #: A named, non-anonymous publisher stands behind the listing.
    has_publisher: bool
    #: The underlying plugin manifest validates against the platform.
    manifest_valid: bool
    #: The requested capabilities were reviewed against what the listing needs.
    capabilities_reviewed: bool
    #: The listing passed a security review.
    security_reviewed: bool
    #: The platform ships this listing itself.
    is_first_party: bool


def assess_certification(signals: CertificationSignals) -> str:
    """Derive a certification tier from evidence. Total and deterministic.

    A first-party listing is ``official`` outright. Otherwise the tier climbs
    only as far as the evidence supports: a broken manifest can never be more
    than ``community``, because nothing above it can be asserted about a listing
    that does not even validate.
    """
    if signals.is_first_party:
        return "official"
    if not (signals.manifest_valid and signals.has_publisher):
        return "community"
    if signals.capabilities_reviewed and signals.security_reviewed:
        return "certified"
    return "verified"


__all__ = [
    "CERTIFICATION_TIERS",
    "CERTIFIED_FLOOR",
    "CertificationSignals",
    "assess_certification",
    "certification_rank",
    "is_certified",
]
