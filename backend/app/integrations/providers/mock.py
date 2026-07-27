"""A deterministic, no-network provider for local development and tests.

It satisfies the full `IntegrationProvider` contract without touching a network,
so the framework's lifecycle — install, refresh, sync, disconnect, retry, health
— can be exercised end to end without a live OAuth app. It is registered only
when `INTEGRATIONS_ENABLE_MOCK` is on, and production refuses that switch.

Determinism is the point: given the same inputs it returns the same tokens and
the same sync outcome, so a test asserts on behaviour rather than on a stub that
happens to return whatever today.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.integrations.framework.types import (
    OAuthTokens,
    ProviderMetadata,
    SyncOutcome,
)

MOCK_PROVIDER_KEY = "mock"


class MockProvider:
    metadata = ProviderMetadata(
        key=MOCK_PROVIDER_KEY,
        name="Mock Integration",
        category="calendar",
        description="A deterministic test integration. Not for production.",
        default_scopes=("mock.read", "mock.write"),
        event_types=("calendar_event.created", "calendar_event.updated"),
    )

    def is_configured(self) -> bool:
        return True

    def authorize_url(
        self, *, redirect_uri: str, state: str, scopes: Sequence[str] | None = None
    ) -> str:
        chosen = ",".join(scopes or self.metadata.default_scopes)
        return (
            "https://mock.integration.test/authorize"
            f"?redirect_uri={redirect_uri}&state={state}&scope={chosen}"
        )

    async def exchange_code(self, *, code: str, redirect_uri: str) -> OAuthTokens:
        return OAuthTokens(
            access_token=f"mock-access-{code}",
            refresh_token="mock-refresh-token",  # noqa: S106 — a test fixture, not a secret
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=self.metadata.default_scopes,
            account_id="mock-account-1",
            account_email="connected@mock.test",
        )

    async def refresh_tokens(self, *, refresh_token: str) -> OAuthTokens:
        return OAuthTokens(
            access_token="mock-access-refreshed",  # noqa: S106 — a test fixture, not a secret
            refresh_token=refresh_token,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=self.metadata.default_scopes,
            account_id="mock-account-1",
            account_email="connected@mock.test",
        )

    async def sync(
        self,
        *,
        access_token: str,
        cursor: str | None,
        event: dict[str, Any] | None,
    ) -> SyncOutcome:
        # A fixed, small batch, advancing the cursor deterministically so a test
        # can prove the resume token round-trips.
        page = int(cursor) + 1 if cursor and cursor.isdigit() else 1
        return SyncOutcome(
            items_processed=3,
            cursor=str(page),
            detail={"triggered_by_event": bool(event)},
        )

    async def revoke(self, *, access_token: str, refresh_token: str | None) -> None:
        return None


__all__ = ["MOCK_PROVIDER_KEY", "MockProvider"]
