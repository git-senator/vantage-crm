"""The provider contract.

Every external service is reached through this one protocol. The framework holds
a provider only as an `IntegrationProvider`, so it can install, refresh, sync and
disconnect any of them without a branch — and a new integration is a new module
that satisfies this interface, touching nothing in the core.

The methods split cleanly into the two things an integration is: an *identity*
handshake (OAuth) and a *data* exchange (sync). A provider that needs neither
half — a static API-key integration, say — still satisfies the shape by raising
a clear error from the half it does not support, which the service turns into a
4xx rather than a 500.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from app.integrations.framework.types import OAuthTokens, ProviderMetadata, SyncOutcome


@runtime_checkable
class IntegrationProvider(Protocol):
    #: Static description used by the catalogue and the install flow.
    metadata: ProviderMetadata

    def is_configured(self) -> bool:
        """Whether this deployment has the credentials to use the provider.

        A provider with no client id is listed in the catalogue but refuses to
        install, so an operator sees *why* it is unavailable rather than a
        provider that silently is not there.
        """
        ...

    def authorize_url(
        self, *, redirect_uri: str, state: str, scopes: Sequence[str] | None = None
    ) -> str:
        """The provider's consent URL to send the user to. Pure and deterministic."""
        ...

    async def exchange_code(self, *, code: str, redirect_uri: str) -> OAuthTokens:
        """Trade an authorization code for tokens (the OAuth callback)."""
        ...

    async def refresh_tokens(self, *, refresh_token: str) -> OAuthTokens:
        """Mint a fresh access token from a stored refresh token."""
        ...

    async def sync(
        self,
        *,
        access_token: str,
        cursor: str | None,
        event: dict[str, Any] | None,
    ) -> SyncOutcome:
        """Exchange data with the provider.

        `cursor` is the resume token from the previous run (None for a full
        sync); `event` is the CRM outbox event when this sync was triggered by
        one, else None. The provider returns what it did and where to resume; it
        never touches the database — persistence and bookkeeping are the
        framework's job, which is what keeps a provider free of tenant concerns.
        """
        ...

    async def revoke(self, *, access_token: str, refresh_token: str | None) -> None:
        """Best-effort revocation at the provider, on disconnect."""
        ...


__all__ = ["IntegrationProvider"]
