"""Shared explainability primitives for the deterministic intelligence engines.

Lead scoring (6.3) and deal health (6.4) — and whatever intelligence comes after
— all owe the same debt: a number a user is asked to act on must be readable back
as the reasons that produced it. These four records are that contract, and they
live here, shared, rather than being redefined per feature, so "explainable"
means the same shape everywhere.

`ScoredSignal` carries points *and* a reason — the same type describes both a
component of a score and a ± adjustment to a probability, because both are "this
much, for this reason". `RiskFlag`, `MissingField` and `Recommendation` are the
non-numeric halves of an explanation: what is wrong, what is absent, what to do,
each stated in words.

Nothing here computes anything. These are data, so the engines that produce them
stay pure and the API layer that renders them has one shape to serialise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Optional localization hooks carried by the explainability records. Engines
#: whose prose is generated (growth intelligence) fill an `i18n_key` and its
#: parameters so the API boundary can render the record in the caller's locale;
#: the per-record engines leave them empty and their English `reason`/`detail`
#: text is used as-is. The default is always English text, never a blank.


@dataclass(frozen=True, slots=True)
class ScoredSignal:
    """One contribution to a score or an adjustment to a probability, with the
    reason it contributed. `points` may be negative."""

    key: str
    label: str
    points: int
    reason: str
    #: Localization key + params for `reason`; empty when the reason is final text.
    i18n_key: str = ""
    i18n_params: dict = field(default_factory=dict)  # type: ignore[type-arg]


@dataclass(frozen=True, slots=True)
class RiskFlag:
    key: str
    label: str
    detail: str
    #: Localization keys for `label` and `detail`, and params for `detail`.
    label_key: str = ""
    detail_key: str = ""
    i18n_params: dict = field(default_factory=dict)  # type: ignore[type-arg]


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
    #: Localization keys for `action` and `reason`; empty when final text.
    action_key: str = ""
    reason_key: str = ""


__all__ = ["MissingField", "Recommendation", "RiskFlag", "ScoredSignal"]
