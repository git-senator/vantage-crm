"""The integration platform (Phase 7.7).

A reusable framework for connecting a tenant's workspace to an external service,
rather than a drawer of one-off integrations. The split is the whole point:

  * `framework` — the provider-agnostic core. The connection lifecycle, OAuth
    handshake shape, token refresh, sync-run bookkeeping, health, and retry all
    live here and know nothing about any particular vendor.
  * `providers` — one module per external service. A provider implements the
    `IntegrationProvider` protocol (OAuth URLs, code exchange, refresh, sync,
    revoke) and declares its metadata. No provider-specific branch ever appears
    in the framework, and the framework never imports a vendor SDK.

Everything else in this codebase is reused, not re-implemented: secrets are
sealed with the shared `SecretBox`, background work rides the existing ARQ
worker and `@job` retry, events arrive through the automation outbox, egress and
lifecycle changes are audited, health alerts go through the notification centre,
and installs are gated on the billing `integrations` entitlement. Every table is
tenant-scoped and RLS-FORCEd.
"""

from __future__ import annotations

from app.integrations.framework.registry import (
    ProviderRegistry,
    build_provider_registry,
)
from app.integrations.framework.types import (
    OAuthTokens,
    ProviderMetadata,
    SyncOutcome,
)

__all__ = [
    "OAuthTokens",
    "ProviderMetadata",
    "ProviderRegistry",
    "SyncOutcome",
    "build_provider_registry",
]
