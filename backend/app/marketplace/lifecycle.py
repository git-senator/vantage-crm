"""The listing publication lifecycle — a small, explicit state machine.

A marketplace listing is not simply present or absent; it moves through a
governed publication path:

    draft -> review -> approved -> published -> deprecated -> retired

A listing is authored as a ``draft``, ``submit``ted for ``review``, ``approve``d
(or rejected back to ``draft``), ``publish``ed so tenants can install it, then
eventually ``deprecate``d (installable no longer, existing installs untouched) and
``retire``d (gone from the catalogue). The transitions are declared once, here, as
pure data — a service asks :func:`next_state` what an action produces and gets
either the new state or a :class:`PublicationError`, so an illegal move (approving
a draft, publishing something never approved) is a precise refusal rather than a
silent inconsistency.

Deterministic and dependency-free, like the SDK and the rest of the marketplace
domain: no imports beyond the standard library.
"""

from __future__ import annotations

#: The persisted publication states, in lifecycle order.
LISTING_STATES: tuple[str, ...] = (
    "draft",
    "review",
    "approved",
    "published",
    "deprecated",
    "retired",
)

#: The publication actions and the (from -> to) transitions each permits.
_TRANSITIONS: dict[str, dict[str, str]] = {
    "submit": {"draft": "review"},
    "approve": {"review": "approved"},
    "reject": {"review": "draft"},
    "publish": {"approved": "published"},
    "deprecate": {"published": "deprecated"},
    "retire": {"published": "retired", "deprecated": "retired"},
}


class PublicationError(Exception):
    """An illegal publication transition was requested."""


def next_state(current: str, action: str) -> str:
    """The state ``action`` produces from ``current``, or raise.

    Idempotence is not silently swallowed: submitting something already in review
    is an error, because the caller believes it is advancing the listing and the
    honest answer is that there is nothing to advance.
    """
    moves = _TRANSITIONS.get(action)
    if moves is None:
        raise PublicationError(f"Unknown publication action '{action}'.")
    target = moves.get(current)
    if target is None:
        raise PublicationError(f"Cannot {action} a listing that is '{current}'.")
    return target


def can_transition(current: str, action: str) -> bool:
    return action in _TRANSITIONS and current in _TRANSITIONS[action]


def is_installable(state: str) -> bool:
    """Whether a tenant may install a listing in this state — published only."""
    return state == "published"


def is_public(state: str) -> bool:
    """Whether a listing is visible in the tenant-facing catalogue. A deprecated
    listing stays visible (existing installs need it) but is not installable; a
    draft/review/approved listing is only visible to its authoring workspace."""
    return state in ("published", "deprecated")


def is_terminal(state: str) -> bool:
    return state == "retired"


__all__ = [
    "LISTING_STATES",
    "PublicationError",
    "can_transition",
    "is_installable",
    "is_public",
    "is_terminal",
    "next_state",
]
