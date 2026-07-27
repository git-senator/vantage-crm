"""The neutral value types the framework and providers exchange.

Deliberately small and provider-free: an `OAuthTokens` is the same shape whether
it came from Google or a mock, and a `SyncOutcome` describes what a sync did
without the framework needing to understand the vendor's payload. Keeping these
as frozen dataclasses (not dicts) means a provider that forgets a field fails at
the boundary rather than three layers later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: CRM outbox event types an integration may react to. The emitter enqueues an
#: integration dispatch only for these — the same static-gate the webhook fan-out
#: uses, so the common case (no integration listening) adds no queue traffic. A
#: connection still subscribes to a subset explicitly; this is only the ceiling.
INTEGRATION_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "client.created",
        "client.updated",
        "calendar_event.created",
        "calendar_event.updated",
    }
)

#: The lifecycle states a connection moves through.
CONNECTION_STATUSES: tuple[str, ...] = (
    "pending",  # OAuth begun, not yet completed
    "active",  # connected and usable
    "error",  # authenticated once, now failing (token/sync)
    "disconnected",  # revoked by the tenant or the provider
)

#: The health a connection reports, derived from recent sync outcomes.
HEALTH_STATES: tuple[str, ...] = ("unknown", "healthy", "degraded", "down")

#: How a sync run was triggered.
SYNC_TRIGGERS: tuple[str, ...] = ("scheduled", "manual", "event")

#: A sync run's own lifecycle.
SYNC_STATUSES: tuple[str, ...] = ("pending", "running", "succeeded", "failed")


@dataclass(frozen=True, slots=True)
class ProviderMetadata:
    """Everything the catalogue and the install flow need to know about a
    provider without instantiating a connection."""

    key: str
    name: str
    #: A coarse grouping for the portal, e.g. `calendar` or `contacts`.
    category: str
    description: str
    #: The OAuth scopes requested by default at install.
    default_scopes: tuple[str, ...]
    #: The CRM events this provider can act on (a subset of the ceiling above).
    event_types: tuple[str, ...] = ()
    docs_url: str | None = None


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    """The result of an authorization-code exchange or a refresh."""

    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: tuple[str, ...] = ()
    account_id: str | None = None
    account_email: str | None = None


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """What one sync did: how much it moved, where to resume, and any detail
    worth keeping on the run record for diagnosis."""

    items_processed: int = 0
    #: An opaque resume token (a page token / sync token). Stored on the
    #: connection so the next incremental sync continues where this stopped.
    cursor: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "CONNECTION_STATUSES",
    "HEALTH_STATES",
    "INTEGRATION_EVENT_TYPES",
    "SYNC_STATUSES",
    "SYNC_TRIGGERS",
    "OAuthTokens",
    "ProviderMetadata",
    "SyncOutcome",
]
