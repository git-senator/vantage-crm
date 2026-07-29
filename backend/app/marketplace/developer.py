"""Developer platform domain — ownership, the application lifecycle, and its rules.

Phase 9.5. The marketplace becomes a developer-facing application platform: a
developer organization authors an application, moves it through a governed
publication path, and holds scoped API credentials. This module is the pure,
deterministic core of that — the state machine, the ownership check, and the
validation rules — with no I/O, in the same spirit as the rest of the marketplace
domain.

The application lifecycle is one step longer than the listing's — it has an
explicit ``submitted`` state before ``review`` so a developer's hand-off to a
reviewer is its own transition:

    draft -> submitted -> review -> approved -> published -> deprecated -> retired
"""

from __future__ import annotations

import re

from app.marketplace.versioning import CompatibilityResult, check_compatibility

#: A developer organization's status.
DEVELOPER_STATES: tuple[str, ...] = ("active", "suspended")

#: The application publication lifecycle, in order.
APPLICATION_STATES: tuple[str, ...] = (
    "draft",
    "submitted",
    "review",
    "approved",
    "published",
    "deprecated",
    "retired",
)

#: The decisions a reviewer can record.
REVIEW_DECISIONS: tuple[str, ...] = ("approved", "rejected")

#: The scopes a developer API credential can carry — a closed set, so an unknown
#: scope is a typo caught at creation rather than a silent over-grant.
DEVELOPER_SCOPES: tuple[str, ...] = (
    "apps.read",
    "apps.write",
    "apps.publish",
    "usage.read",
)

_APP_TRANSITIONS: dict[str, dict[str, str]] = {
    "submit": {"draft": "submitted"},
    "begin_review": {"submitted": "review"},
    "approve": {"review": "approved"},
    "reject": {"submitted": "draft", "review": "draft"},
    "publish": {"approved": "published"},
    "deprecate": {"published": "deprecated"},
    "retire": {"published": "retired", "deprecated": "retired"},
}

_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,48}[a-z0-9]$")


class ApplicationLifecycleError(Exception):
    """An illegal application lifecycle transition was requested."""


def next_app_state(current: str, action: str) -> str:
    """The state ``action`` produces from ``current``, or raise."""
    moves = _APP_TRANSITIONS.get(action)
    if moves is None:
        raise ApplicationLifecycleError(f"Unknown application action '{action}'.")
    target = moves.get(current)
    if target is None:
        raise ApplicationLifecycleError(
            f"Cannot {action} an application that is '{current}'."
        )
    return target


def is_published(state: str) -> bool:
    return state == "published"


def is_installable(state: str) -> bool:
    return state == "published"


def is_under_review(state: str) -> bool:
    """Whether the application is somewhere in the review pipeline."""
    return state in ("submitted", "review")


def can_publish(state: str) -> bool:
    return state == "approved"


def developer_owns(developer_org_id: object, application_developer_org_id: object) -> bool:
    """Whether a developer organization owns an application."""
    return developer_org_id == application_developer_org_id


def validate_slug(slug: str) -> str:
    if not _SLUG_RE.match(slug):
        raise ValueError(
            "Application slug must be lower kebab-case, 3-50 chars."
        )
    return slug


def validate_scopes(scopes: list[str]) -> list[str]:
    """Return the scopes sorted and de-duplicated, or raise on an unknown one."""
    for scope in scopes:
        if scope not in DEVELOPER_SCOPES:
            raise ValueError(f"Unknown developer scope '{scope}'.")
    return sorted(set(scopes))


def validate_app_metadata(metadata: dict[str, object]) -> dict[str, object]:
    """Validate an application's metadata, raising ``ValueError`` on any problem.

    A ``display_name`` and a ``summary`` are required — the two fields a
    marketplace listing cannot be shown without. Everything else is optional and
    passes through untouched.
    """
    display_name = metadata.get("display_name")
    if not isinstance(display_name, str) or not (1 <= len(display_name) <= 120):
        raise ValueError("Application metadata needs a 'display_name' (1-120 chars).")
    summary = metadata.get("summary")
    if not isinstance(summary, str) or not (1 <= len(summary) <= 300):
        raise ValueError("Application metadata needs a 'summary' (1-300 chars).")
    return metadata


def application_compatibility(metadata: dict[str, object]) -> CompatibilityResult:
    """Judge an application's SDK compatibility from its metadata, reusing the
    SDK compatibility layer. Metadata that names no SDK is compatible."""
    version = str(metadata.get("version") or "1.0.0")
    return check_compatibility(version, metadata)


__all__ = [
    "APPLICATION_STATES",
    "DEVELOPER_SCOPES",
    "DEVELOPER_STATES",
    "REVIEW_DECISIONS",
    "ApplicationLifecycleError",
    "application_compatibility",
    "can_publish",
    "developer_owns",
    "is_installable",
    "is_published",
    "is_under_review",
    "next_app_state",
    "validate_app_metadata",
    "validate_scopes",
    "validate_slug",
]
