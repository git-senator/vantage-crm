"""Integration platform (Phase 7.7).

The properties that carry the milestone:

  * **The framework is provider-agnostic.** The mock provider drives the whole
    lifecycle — install, refresh, sync, disconnect, health — without a network,
    and the service never branches on which provider it holds.
  * **Tokens are encrypted at rest.** After an install the stored access/refresh
    tokens are `SecretBox` tokens, never plaintext, and no read projection
    carries them.
  * **Token refresh is transparent.** A stale token is refreshed and re-sealed
    before a sync, and the refresh is audited.
  * **Health degrades and auto-disables.** Repeated terminal failures disable a
    connection and notify its owner.
  * **Tenant isolation holds** — a connection is invisible to another workspace —
    and installs are gated on the billing `integrations` entitlement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.permissions import Scope
from app.core.secrets import is_token
from app.integrations.framework.registry import ProviderRegistry
from app.integrations.framework.types import SyncOutcome
from app.integrations.providers.google.calendar import GoogleCalendarProvider
from app.integrations.providers.mock import MockProvider
from app.models.audit import AuditLog
from app.models.integration import IntegrationConnection, IntegrationSyncRun
from app.models.notification import Notification
from app.models.organization import Organization
from app.services.integration import (
    ACCESS_CONTEXT,
    IntegrationService,
    IntegrationSyncService,
)
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "JWT_SECRET": SecretStr(TEST_JWT_SECRET),
        "ENVIRONMENT": "test",
        "INTEGRATIONS_ENABLE_MOCK": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _registry() -> ProviderRegistry:
    return ProviderRegistry({MockProvider().metadata.key: MockProvider()})


def _auth(organization: Organization, user_id, **grants: Scope) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    resolved = {"settings.manage": Scope.ALL, **grants}
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants=resolved,
    )


def _service(  # type: ignore[no-untyped-def]
    db: AsyncSession, organization: Organization, user_id, **s: object
) -> IntegrationService:
    return IntegrationService(
        db, _auth(organization, user_id), _settings(**s), _registry()
    )


# ------------------------------------------------------- provider framework
# Pure; no database.


class TestProviders:
    async def test_mock_provider_round_trips(self) -> None:
        provider = MockProvider()
        assert provider.is_configured()
        url = provider.authorize_url(redirect_uri="https://app.test/cb", state="xyz")
        assert "state=xyz" in url and "redirect_uri=https://app.test/cb" in url

        tokens = await provider.exchange_code(code="abc", redirect_uri="https://app.test/cb")
        assert tokens.access_token == "mock-access-abc"
        assert tokens.refresh_token
        assert tokens.account_email == "connected@mock.test"

        outcome = await provider.sync(access_token="t", cursor=None, event=None)
        assert outcome.items_processed == 3
        assert outcome.cursor == "1"
        # The cursor advances deterministically.
        assert (await provider.sync(access_token="t", cursor="1", event=None)).cursor == "2"

    def test_google_provider_metadata_and_config(self) -> None:
        unconfigured = GoogleCalendarProvider(_settings())
        assert unconfigured.metadata.key == "google_calendar"
        assert not unconfigured.is_configured()

        configured = GoogleCalendarProvider(
            _settings(GOOGLE_CLIENT_ID="cid", GOOGLE_CLIENT_SECRET=SecretStr("secret"))
        )
        assert configured.is_configured()
        url = configured.authorize_url(
            redirect_uri="https://app.test/cb", state="st8"
        )
        assert "client_id=cid" in url and "state=st8" in url
        assert "accounts.google.com" in url

    def test_registry_rejects_unknown_provider(self) -> None:
        with pytest.raises(NotFoundError):
            _registry().get("does-not-exist")


# ------------------------------------------------------------ install flow


class TestInstall:
    async def test_install_seals_tokens_and_activates(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "install@vantage.example")
        service = _service(db, organization, user.id)

        start = await service.begin_install(
            user, provider="mock", redirect_uri="https://app.test/cb"
        )
        assert "state=" in start.authorize_url

        connection = await service.complete_install(
            user, state=start.state, code="the-code"
        )
        assert connection.status == "active"
        assert connection.external_account_email == "connected@mock.test"
        # The read projection carries no token at all.
        assert not hasattr(connection, "access_token")

        row = (
            await db.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.id == connection.id
                )
            )
        ).scalar_one()
        # Stored tokens are sealed, not plaintext.
        assert row.access_token is not None and is_token(row.access_token)
        assert row.refresh_token is not None and is_token(row.refresh_token)
        # And they open back to what the provider issued.
        from app.core.secrets import get_secret_box

        assert (
            get_secret_box().decrypt(row.access_token, context=ACCESS_CONTEXT)
            == "mock-access-the-code"
        )

    async def test_complete_install_is_idempotent(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "idem@vantage.example")
        service = _service(db, organization, user.id)
        start = await service.begin_install(
            user, provider="mock", redirect_uri="https://app.test/cb"
        )
        first = await service.complete_install(user, state=start.state, code="c1")
        # The state was cleared; completing again with it is a clean not-found,
        # never a second code exchange.
        with pytest.raises(NotFoundError):
            await service.complete_install(user, state=start.state, code="c2")
        assert first.status == "active"

    async def test_begin_reuses_a_pending_connection(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "reuse@vantage.example")
        service = _service(db, organization, user.id)
        a = await service.begin_install(
            user, provider="mock", redirect_uri="https://app.test/cb"
        )
        b = await service.begin_install(
            user, provider="mock", redirect_uri="https://app.test/cb"
        )
        assert a.connection_id == b.connection_id  # no orphaned pending rows

    async def test_install_requires_a_redirect(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "noredir@vantage.example")
        service = _service(db, organization, user.id)
        with pytest.raises(AppError):
            await service.begin_install(user, provider="mock")


# ------------------------------------------------ subscriptions & disconnect


class TestLifecycle:
    async def _connect(self, db: AsyncSession, organization: Organization, email: str):  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, email)
        service = _service(db, organization, user.id)
        start = await service.begin_install(
            user, provider="mock", redirect_uri="https://app.test/cb"
        )
        connection = await service.complete_install(user, state=start.state, code="c")
        return user, service, connection

    async def test_set_and_list_subscriptions(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user, service, connection = await self._connect(
            db, organization, "subs@vantage.example"
        )
        subs = await service.set_subscriptions(
            user, connection.id, ["calendar_event.created"]
        )
        assert [s.event_type for s in subs] == ["calendar_event.created"]
        listed = await service.list_subscriptions(connection.id)
        assert len(listed) == 1

    async def test_unknown_event_is_rejected(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user, service, connection = await self._connect(
            db, organization, "badsub@vantage.example"
        )
        with pytest.raises(AppError):
            await service.set_subscriptions(user, connection.id, ["deal.won"])

    async def test_disconnect_clears_tokens(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user, service, connection = await self._connect(
            db, organization, "disc@vantage.example"
        )
        result = await service.disconnect(user, connection.id)
        assert result.status == "disconnected"

        row = (
            await db.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.id == connection.id
                )
            )
        ).scalar_one()
        assert row.access_token is None and row.refresh_token is None

    async def test_trigger_sync_creates_a_pending_run(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        _user, service, connection = await self._connect(
            db, organization, "sync@vantage.example"
        )
        result = await service.trigger_sync(connection.id, trigger="manual")
        assert result.status == "pending"
        run = (
            await db.execute(
                select(IntegrationSyncRun).where(
                    IntegrationSyncRun.id == result.run_id
                )
            )
        ).scalar_one()
        assert run.trigger == "manual"

    async def test_sync_on_inactive_connection_is_rejected(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user, service, connection = await self._connect(
            db, organization, "inactive@vantage.example"
        )
        await service.disconnect(user, connection.id)
        with pytest.raises(ConflictError):
            await service.trigger_sync(connection.id)


# -------------------------------------------------------- sync execution


class TestSyncExecution:
    async def _connection(self, db: AsyncSession, organization: Organization, email: str):  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, email)
        service = _service(db, organization, user.id)
        start = await service.begin_install(
            user, provider="mock", redirect_uri="https://app.test/cb"
        )
        read = await service.complete_install(user, state=start.state, code="c")
        connection = (
            await db.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.id == read.id
                )
            )
        ).scalar_one()
        return user, connection

    async def test_record_success_marks_healthy_and_stores_cursor(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        _user, connection = await self._connection(db, organization, "ok@vantage.example")
        run = IntegrationSyncRun(
            organization_id=organization.id,
            connection_id=connection.id,
            trigger="manual",
            status="pending",
        )
        db.add(run)
        await db.flush()

        sync = IntegrationSyncService(db, _settings(), _registry())
        await sync.begin_run(run)
        assert run.status == "running" and run.attempts == 1
        await sync.record_success(
            connection, run, SyncOutcome(items_processed=5, cursor="99")
        )
        assert run.status == "succeeded" and run.items_processed == 5
        assert connection.health == "healthy"
        assert connection.config.get("cursor") == "99"
        assert connection.last_sync_at is not None

    async def test_stale_token_is_refreshed_and_resealed(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        _user, connection = await self._connection(db, organization, "stale@vantage.example")
        # Force the token to look expired.
        connection.token_expires_at = datetime.now(UTC) - timedelta(minutes=5)
        await db.flush()

        sync = IntegrationSyncService(db, _settings(), _registry())
        token = await sync.access_token_for(connection)
        assert token == "mock-access-refreshed"

        from app.core.secrets import get_secret_box

        assert connection.access_token is not None
        assert (
            get_secret_box().decrypt(connection.access_token, context=ACCESS_CONTEXT)
            == "mock-access-refreshed"
        )
        refresh_audit = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "integration.token_refreshed"
                )
            )
        ).scalars().all()
        assert len(refresh_audit) == 1

    async def test_terminal_failures_disable_and_notify(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user, connection = await self._connection(db, organization, "down@vantage.example")
        run = IntegrationSyncRun(
            organization_id=organization.id,
            connection_id=connection.id,
            trigger="scheduled",
            status="running",
        )
        db.add(run)
        await db.flush()

        # Disable after a single terminal failure.
        sync = IntegrationSyncService(
            db, _settings(INTEGRATION_DISABLE_AFTER_FAILURES=1), _registry()
        )
        disabled = await sync.record_failure(
            connection, run, error="boom", terminal=True
        )
        assert disabled is True
        assert connection.status == "error" and connection.health == "down"
        assert connection.disabled_at is not None
        assert run.status == "failed"

        # The owner was notified, and the disable was audited.
        note = (
            await db.execute(
                select(Notification).where(
                    Notification.type == "integration.disabled"
                )
            )
        ).scalars().all()
        assert len(note) == 1 and note[0].user_id == user.id
        audited = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "integration.disabled")
            )
        ).scalars().all()
        assert len(audited) == 1

    async def test_non_terminal_failure_keeps_running_without_disabling(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        _user, connection = await self._connection(db, organization, "retry@vantage.example")
        run = IntegrationSyncRun(
            organization_id=organization.id,
            connection_id=connection.id,
            trigger="scheduled",
            status="running",
        )
        db.add(run)
        await db.flush()

        sync = IntegrationSyncService(
            db, _settings(INTEGRATION_DISABLE_AFTER_FAILURES=3), _registry()
        )
        disabled = await sync.record_failure(
            connection, run, error="transient", terminal=False
        )
        assert disabled is False
        assert connection.status == "active"  # still usable
        assert connection.consecutive_failures == 0  # only terminal bumps it
        assert connection.health == "degraded"


# ----------------------------------------------------- catalogue & isolation


class TestCatalogueAndIsolation:
    async def test_provider_catalogue_reports_connected(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "cat@vantage.example")
        service = _service(db, organization, user.id)
        providers = await service.list_providers()
        assert [p.key for p in providers] == ["mock"]
        assert providers[0].configured and not providers[0].connected

        start = await service.begin_install(
            user, provider="mock", redirect_uri="https://app.test/cb"
        )
        await service.complete_install(user, state=start.state, code="c")
        assert (await service.list_providers())[0].connected

    async def test_a_connection_is_invisible_to_another_workspace(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        mine = await make_user(db, organization, "mine@vantage.example")
        service = _service(db, organization, mine.id)
        start = await service.begin_install(
            mine, provider="mock", redirect_uri="https://app.test/cb"
        )
        connection = await service.complete_install(mine, state=start.state, code="c")

        theirs = await make_user(db, other_organization, "theirs@meridian.example")
        their_service = _service(db, other_organization, theirs.id)
        assert await their_service.list_connections() == []
        with pytest.raises(NotFoundError):
            await their_service.get_connection(connection.id)

    async def test_install_gated_on_billing_entitlement(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "billing@vantage.example")
        # Enforcement on, no plan seeded -> the default (free) plan lacks the
        # integrations feature, so the install is refused.
        service = _service(db, organization, user.id, BILLING_ENFORCED=True)
        with pytest.raises(PermissionDeniedError):
            await service.begin_install(
                user, provider="mock", redirect_uri="https://app.test/cb"
            )
