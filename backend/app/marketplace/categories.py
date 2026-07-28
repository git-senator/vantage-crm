"""The integration category taxonomy.

The plugin platform (Phase 9.0) has a deliberately coarse category set —
``integration``, ``messaging``, ``ai`` and so on — because a plugin is a broad
thing. A *marketplace* wants a finer grouping so a workspace can find "the
e-signature integrations" rather than scrolling every "integration". This is that
finer, closed taxonomy: a listing carries one of these, independent of the coarse
category its underlying plugin declares.

Closed for the same reason every other vocabulary here is closed — an unknown
category on a listing is a typo caught at sync time, not a silent bucket nobody
can browse to.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IntegrationCategory:
    key: str
    title: str
    description: str


def _c(key: str, title: str, description: str) -> IntegrationCategory:
    return IntegrationCategory(key=key, title=title, description=description)


INTEGRATION_CATEGORIES: dict[str, IntegrationCategory] = {
    c.key: c
    for c in (
        _c("crm", "CRM & sales", "Sync records with another sales system."),
        _c("calendar", "Calendar", "Two-way calendar and scheduling."),
        _c("email", "Email", "Send and track email from the workspace."),
        _c("messaging", "Messaging", "Post to chat and messaging channels."),
        _c("payments", "Payments & billing", "Invoicing, subscriptions and payments."),
        _c("esignature", "E-signature", "Send and track documents for signature."),
        _c("telephony", "Telephony & SMS", "Voice and text messaging providers."),
        _c("productivity", "Productivity", "Docs, contacts and workspace suites."),
        _c("ai", "AI", "Model providers for the AI features."),
        _c("automation", "Automation", "No-code automation and workflow hubs."),
        _c("analytics", "Analytics", "Reporting and business-intelligence tools."),
        _c("storage", "Storage", "File storage and document management."),
        _c("other", "Other", "Anything the fixed categories do not cover."),
    )
}


def integration_category(key: str) -> IntegrationCategory:
    """Look up a category. Raises ``KeyError`` for an unknown one."""
    try:
        return INTEGRATION_CATEGORIES[key]
    except KeyError as exc:
        raise KeyError(f"Unknown integration category '{key}'.") from exc


def is_known_category(key: str) -> bool:
    return key in INTEGRATION_CATEGORIES


__all__ = [
    "INTEGRATION_CATEGORIES",
    "IntegrationCategory",
    "integration_category",
    "is_known_category",
]
