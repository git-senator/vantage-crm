"""Integration services — the provider-agnostic lifecycle.

Two halves, the same split the webhook subsystem makes:

**Management** (`IntegrationService`) runs in a tenant-bound user session, gated on
`settings.manage` — connecting a workspace to an external service is an egress
decision. It drives the OAuth handshake, seals the returned tokens with the
shared `SecretBox`, manages event subscriptions, triggers syncs, and reports
health. Every lifecycle change is audited; installs are gated on the billing
`integrations` entitlement. It never talks to a vendor directly — it calls the
provider through the injected `ProviderRegistry`.

**Sync execution** (`IntegrationSyncService`) is the machine half the jobs use
under a system context. It refreshes an access token when it is stale, hands the
provider the plaintext token to sync with, and records each run's outcome —
advancing a connection's health and failure streak, and auto-disabling one whose
receiver has been broken too long, with a notification to its owner.

Neither half persists a token in the clear or lets one reach a read projection.
"""

from __future__ import annotations

import secrets as pysecrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.secrets import get_secret_box
from app.integrations.framework.registry import ProviderRegistry, build_provider_registry
from app.integrations.framework.types import SyncOutcome
from app.models.integration import (
    IntegrationConnection,
    IntegrationSubscription,
    IntegrationSyncRun,
)
from app.models.user import User
from app.repositories.integration import (
    IntegrationConnectionRepository,
    IntegrationSubscriptionRepository,
    IntegrationSyncRunRepository,
)
from app.schemas.common import Cursor
from app.schemas.integration import (
    ConnectionHealth,
    ConnectionRead,
    InstallStartResult,
    ProviderRead,
    SubscriptionRead,
    SyncRunRead,
    SyncTriggerResult,
)
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext

logger = get_logger(__name__)

MANAGE_PERMISSION = "settings.manage"
ENTITY_TYPE = "integration"
FEATURE = "integrations"

#: Bound into each sealed token so a value lifted into another column fails to
#: open there — the same defence the MFA and webhook secrets use.
ACCESS_CONTEXT = "integration_connections.access_token"
REFRESH_CONTEXT = "integration_connections.refresh_token"


def to_connection_read(connection: IntegrationConnection) -> ConnectionRead:
    """A read projection with no token, by construction."""
    return ConnectionRead(
        id=connection.id,
        provider=connection.provider,
        status=connection.status,
        external_account_email=connection.external_account_email,
        external_account_id=connection.external_account_id,
        scopes=list(connection.scopes),
        health=connection.health,
        last_sync_at=connection.last_sync_at,
        last_error=connection.last_error,
        consecutive_failures=connection.consecutive_failures,
        disabled_at=connection.disabled_at,
        is_active=connection.is_active,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


class IntegrationService:
    def __init__(
        self,
        session: AsyncSession,
        auth: AuthorizationContext,
        settings: Settings,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.registry = registry or build_provider_registry(settings)
        self.connections = IntegrationConnectionRepository(session)
        self.subscriptions = IntegrationSubscriptionRepository(session)
        self.runs = IntegrationSyncRunRepository(session)
        self.audit = AuditService(session)
        self._box = get_secret_box()

    # -------------------------------------------------------- catalogue

    async def list_providers(self) -> list[ProviderRead]:
        self.auth.require(MANAGE_PERMISSION)
        connected = {
            c.provider
            for c in await self.connections.list_for_org(self.auth.organization_id)
            if c.is_active
        }
        result: list[ProviderRead] = []
        for provider in self.registry.all():
            meta = provider.metadata
            result.append(
                ProviderRead(
                    key=meta.key,
                    name=meta.name,
                    category=meta.category,
                    description=meta.description,
                    default_scopes=list(meta.default_scopes),
                    event_types=list(meta.event_types),
                    docs_url=meta.docs_url,
                    configured=provider.is_configured(),
                    connected=meta.key in connected,
                )
            )
        return result

    # ------------------------------------------------------- connections

    async def list_connections(self) -> list[ConnectionRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.connections.list_for_org(self.auth.organization_id)
        return [to_connection_read(row) for row in rows]

    async def get_connection(self, connection_id: UUID) -> ConnectionRead:
        self.auth.require(MANAGE_PERMISSION)
        return to_connection_read(await self._load(connection_id))

    # ---------------------------------------------------------- install

    async def begin_install(
        self,
        actor: User,
        *,
        provider: str,
        redirect_uri: str | None = None,
        scopes: list[str] | None = None,
    ) -> InstallStartResult:
        """Start the OAuth handshake. Reuses a pending install for the provider
        so beginning twice does not orphan the first."""
        self.auth.require(MANAGE_PERMISSION)
        await self._enforce_billing()

        provider_obj = self.registry.get(provider)
        if not provider_obj.is_configured():
            raise ConflictError(
                f"The '{provider}' integration is not configured on this deployment."
            )
        redirect = self._resolve_redirect(redirect_uri)
        chosen = scopes or list(provider_obj.metadata.default_scopes)
        state = pysecrets.token_urlsafe(24)

        connection = await self.connections.get_pending(
            provider, self.auth.organization_id
        )
        if connection is None:
            connection = IntegrationConnection(
                organization_id=self.auth.organization_id,
                created_by=actor.id,
                provider=provider,
                status="pending",
            )
            self.session.add(connection)
        connection.oauth_state = state
        connection.scopes = chosen
        connection.config = {**(connection.config or {}), "redirect_uri": redirect}
        await self.session.flush()

        authorize_url = provider_obj.authorize_url(
            redirect_uri=redirect, state=state, scopes=chosen
        )
        logger.info(
            "integration_install_started",
            extra={"connection_id": str(connection.id), "provider": provider},
        )
        return InstallStartResult(
            connection_id=connection.id, authorize_url=authorize_url, state=state
        )

    async def complete_install(
        self,
        actor: User,
        *,
        state: str,
        code: str,
        redirect_uri: str | None = None,
    ) -> ConnectionRead:
        """Finish the handshake: exchange the code, seal the tokens, activate.

        Idempotent — a connection already active for the state is returned
        unchanged rather than re-exchanging a spent code.
        """
        self.auth.require(MANAGE_PERMISSION)
        connection = await self.connections.get_by_state(
            state, self.auth.organization_id
        )
        if connection is None:
            raise NotFoundError("No pending connection for this authorization.")
        if connection.status == "active":
            return to_connection_read(connection)

        provider_obj = self.registry.get(connection.provider)
        redirect = (
            redirect_uri
            or connection.config.get("redirect_uri")
            or self._resolve_redirect(None)
        )
        tokens = await provider_obj.exchange_code(code=code, redirect_uri=redirect)

        connection.access_token = self._box.encrypt(
            tokens.access_token, context=ACCESS_CONTEXT
        )
        connection.refresh_token = (
            self._box.encrypt(tokens.refresh_token, context=REFRESH_CONTEXT)
            if tokens.refresh_token
            else None
        )
        connection.token_expires_at = tokens.expires_at
        connection.external_account_id = tokens.account_id
        connection.external_account_email = tokens.account_email
        if tokens.scopes:
            connection.scopes = list(tokens.scopes)
        connection.status = "active"
        connection.health = "healthy"
        connection.last_error = None
        connection.consecutive_failures = 0
        connection.disabled_at = None
        connection.oauth_state = None
        await self.session.flush()
        await self._reload(connection)

        await self._audit(AuditAction.INTEGRATION_CONNECTED, actor, connection)
        logger.info(
            "integration_connected",
            extra={"connection_id": str(connection.id), "provider": connection.provider},
        )
        return to_connection_read(connection)

    async def disconnect(self, actor: User, connection_id: UUID) -> ConnectionRead:
        """Revoke at the provider (best-effort) and tear the connection down."""
        self.auth.require(MANAGE_PERMISSION)
        connection = await self._load(connection_id)

        if connection.access_token:
            provider_obj = self.registry.get(connection.provider)
            try:
                await provider_obj.revoke(
                    access_token=self._box.decrypt(
                        connection.access_token, context=ACCESS_CONTEXT
                    ),
                    refresh_token=(
                        self._box.decrypt(
                            connection.refresh_token, context=REFRESH_CONTEXT
                        )
                        if connection.refresh_token
                        else None
                    ),
                )
            except Exception:  # pragma: no cover — revocation is best-effort
                logger.warning("integration_revoke_failed", exc_info=True)

        connection.status = "disconnected"
        connection.disabled_at = datetime.now(UTC)
        connection.access_token = None
        connection.refresh_token = None
        connection.token_expires_at = None
        connection.health = "unknown"
        await self.session.flush()
        await self._reload(connection)

        await self._audit(AuditAction.INTEGRATION_DISCONNECTED, actor, connection)
        logger.info("integration_disconnected", extra={"connection_id": str(connection.id)})
        return to_connection_read(connection)

    # ------------------------------------------------------ subscriptions

    async def list_subscriptions(self, connection_id: UUID) -> list[SubscriptionRead]:
        self.auth.require(MANAGE_PERMISSION)
        await self._load(connection_id)
        rows = await self.subscriptions.list_for_connection(
            connection_id, self.auth.organization_id
        )
        return [_subscription_read(row) for row in rows]

    async def set_subscriptions(
        self, actor: User, connection_id: UUID, event_types: list[str]
    ) -> list[SubscriptionRead]:
        """Replace a connection's event subscriptions with the given set."""
        self.auth.require(MANAGE_PERMISSION)
        connection = await self._load(connection_id)
        allowed = set(self.registry.get(connection.provider).metadata.event_types)
        wanted = sorted(set(event_types))
        for event_type in wanted:
            if event_type not in allowed:
                raise AppError(
                    f"'{connection.provider}' cannot subscribe to '{event_type}'."
                )

        await self.subscriptions.delete_for_connection(
            connection_id, self.auth.organization_id
        )
        created: list[IntegrationSubscription] = []
        for event_type in wanted:
            row = IntegrationSubscription(
                organization_id=self.auth.organization_id,
                connection_id=connection_id,
                event_type=event_type,
            )
            self.session.add(row)
            created.append(row)
        await self.session.flush()

        await self._audit(
            AuditAction.INTEGRATION_UPDATED,
            actor,
            connection,
            extra={"subscriptions": wanted},
        )
        return [_subscription_read(row) for row in created]

    # ------------------------------------------------------------- sync

    async def trigger_sync(
        self, connection_id: UUID, *, trigger: str = "manual", event_type: str | None = None
    ) -> SyncTriggerResult:
        """Queue a sync for a connection. Returns the created run."""
        self.auth.require(MANAGE_PERMISSION)
        connection = await self._load(connection_id)
        if not connection.is_active:
            raise ConflictError("This connection is not active.")

        run = await self._create_run(connection, trigger=trigger, event_type=event_type)
        await self._enqueue_run(connection, run)
        return SyncTriggerResult(run_id=run.id, status=run.status)

    async def list_runs(
        self, connection_id: UUID, *, limit: int, cursor: Cursor | None
    ) -> tuple[list[SyncRunRead], bool]:
        self.auth.require(MANAGE_PERMISSION)
        await self._load(connection_id)
        rows, has_more = await self.runs.list_for_connection(
            connection_id, self.auth.organization_id, limit=limit, cursor=cursor
        )
        return [_sync_run_read(row) for row in rows], has_more

    async def health(self, connection_id: UUID) -> ConnectionHealth:
        self.auth.require(MANAGE_PERMISSION)
        connection = await self._load(connection_id)
        counts = await self.runs.count_by_status(
            self.auth.organization_id, connection_id=connection_id
        )
        return ConnectionHealth(
            connection_id=connection.id,
            status=connection.status,
            health=connection.health,
            last_sync_at=connection.last_sync_at,
            last_error=connection.last_error,
            consecutive_failures=connection.consecutive_failures,
            runs_by_status=counts,
        )

    # ---------------------------------------------------------- helpers

    async def _create_run(
        self, connection: IntegrationConnection, *, trigger: str, event_type: str | None
    ) -> IntegrationSyncRun:
        run = IntegrationSyncRun(
            organization_id=self.auth.organization_id,
            connection_id=connection.id,
            trigger=trigger,
            event_type=event_type,
            status="pending",
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def _enqueue_run(
        self, connection: IntegrationConnection, run: IntegrationSyncRun
    ) -> None:
        from app.workers.queue import JobName, enqueue

        await enqueue(
            JobName.RUN_INTEGRATION_SYNC,
            str(connection.id),
            str(self.auth.organization_id),
            str(run.id),
            job_id=f"intsync:{run.id}",
        )

    async def _enforce_billing(self) -> None:
        if not self.settings.BILLING_ENFORCED:
            return
        from app.services.billing.service import EntitlementService

        await EntitlementService(self.session, self.auth).require(FEATURE)

    def _resolve_redirect(self, redirect_uri: str | None) -> str:
        redirect = redirect_uri or self.settings.GOOGLE_OAUTH_REDIRECT_URI
        if not redirect:
            raise AppError(
                "No OAuth redirect URI. Pass redirect_uri or set "
                "GOOGLE_OAUTH_REDIRECT_URI."
            )
        return redirect

    async def _load(self, connection_id: UUID) -> IntegrationConnection:
        connection = await self.connections.get(
            connection_id, self.auth.organization_id
        )
        if connection is None:
            raise NotFoundError("Integration connection not found.")
        return connection

    async def _reload(self, connection: IntegrationConnection) -> None:
        await self.session.refresh(connection, ["updated_at"])

    async def _audit(
        self,
        action: str,
        actor: User,
        connection: IntegrationConnection,
        *,
        extra: dict[str, object] | None = None,
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=connection.id,
            metadata={
                "provider": connection.provider,
                "status": connection.status,
                "account": connection.external_account_email,
                **(extra or {}),
            },
        )


class IntegrationSyncService:
    """The machine half the sync jobs use. No `settings.manage` gate: it runs
    under a system context bound to one tenant and takes no user-facing action."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.registry = registry or build_provider_registry(settings)
        self.connections = IntegrationConnectionRepository(session)
        self.runs = IntegrationSyncRunRepository(session)
        self.audit = AuditService(session)
        self._box = get_secret_box()

    async def load(
        self, run_id: UUID, organization_id: UUID
    ) -> tuple[IntegrationSyncRun, IntegrationConnection] | None:
        run = await self.runs.get(run_id, organization_id)
        if run is None:
            return None
        connection = await self.connections.get(run.connection_id, organization_id)
        if connection is None:  # pragma: no cover — CASCADE keeps these together
            return None
        return run, connection

    async def begin_run(self, run: IntegrationSyncRun) -> None:
        run.status = "running"
        run.attempts += 1
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        await self.session.flush()

    async def access_token_for(self, connection: IntegrationConnection) -> str:
        """The connection's access token, refreshed and re-sealed if stale."""
        if connection.access_token is None:
            raise AppError("Connection has no access token.")
        access = self._box.decrypt(connection.access_token, context=ACCESS_CONTEXT)
        if not (connection.token_is_expired() and connection.refresh_token):
            return access

        provider_obj = self.registry.get(connection.provider)
        refresh = self._box.decrypt(connection.refresh_token, context=REFRESH_CONTEXT)
        tokens = await provider_obj.refresh_tokens(refresh_token=refresh)
        connection.access_token = self._box.encrypt(
            tokens.access_token, context=ACCESS_CONTEXT
        )
        if tokens.refresh_token:
            connection.refresh_token = self._box.encrypt(
                tokens.refresh_token, context=REFRESH_CONTEXT
            )
        connection.token_expires_at = tokens.expires_at
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.INTEGRATION_TOKEN_REFRESHED,
            organization_id=connection.organization_id,
            actor_id=None,
            actor_email="system",
            entity_type=ENTITY_TYPE,
            entity_id=connection.id,
            metadata={"provider": connection.provider},
        )
        logger.info(
            "integration_token_refreshed",
            extra={"connection_id": str(connection.id)},
        )
        return tokens.access_token

    async def record_success(
        self,
        connection: IntegrationConnection,
        run: IntegrationSyncRun,
        outcome: SyncOutcome,
    ) -> None:
        now = datetime.now(UTC)
        run.status = "succeeded"
        run.items_processed = outcome.items_processed
        run.cursor = outcome.cursor
        run.error = None
        run.finished_at = now

        connection.last_sync_at = now
        connection.consecutive_failures = 0
        connection.health = "healthy"
        connection.last_error = None
        if outcome.cursor is not None:
            connection.config = {**(connection.config or {}), "cursor": outcome.cursor}
        await self.session.flush()

    async def record_failure(
        self,
        connection: IntegrationConnection,
        run: IntegrationSyncRun,
        *,
        error: str,
        terminal: bool,
    ) -> bool:
        """Record a failed attempt. Returns whether the connection was disabled.

        On a non-terminal failure the run stays `running` (the job will retry);
        only a terminal failure marks it `failed` and moves the connection's
        health and failure streak — so the auto-disable threshold counts dead
        syncs, not retries of one.
        """
        now = datetime.now(UTC)
        run.error = error[:2000]
        connection.last_error = error[:2000]
        connection.health = "degraded"

        if not terminal:
            await self.session.flush()
            return False

        run.status = "failed"
        run.finished_at = now
        connection.consecutive_failures += 1

        disabled = False
        if connection.consecutive_failures >= self.settings.INTEGRATION_DISABLE_AFTER_FAILURES:
            connection.status = "error"
            connection.disabled_at = now
            connection.health = "down"
            disabled = True
        await self.session.flush()

        if disabled:
            await self._on_disabled(connection)
        return disabled

    async def _on_disabled(self, connection: IntegrationConnection) -> None:
        await self.audit.record(
            action=AuditAction.INTEGRATION_DISABLED,
            organization_id=connection.organization_id,
            actor_id=None,
            actor_email="system",
            entity_type=ENTITY_TYPE,
            entity_id=connection.id,
            metadata={
                "provider": connection.provider,
                "consecutive_failures": connection.consecutive_failures,
            },
        )
        logger.warning(
            "integration_auto_disabled",
            extra={
                "connection_id": str(connection.id),
                "consecutive_failures": connection.consecutive_failures,
            },
        )
        if connection.created_by is not None:
            from app.services.notification_center import NotificationCenter

            await NotificationCenter(self.session).raise_notification(
                organization_id=connection.organization_id,
                recipient_id=connection.created_by,
                category="system",
                type="integration.disabled",
                title=f"{connection.provider} integration disabled",
                body=(
                    "Syncing was turned off after repeated failures. "
                    "Reconnect the integration to resume."
                ),
                entity_type=ENTITY_TYPE,
                entity_id=connection.id,
            )


def _subscription_read(row: IntegrationSubscription) -> SubscriptionRead:
    return SubscriptionRead(
        id=row.id,
        connection_id=row.connection_id,
        event_type=row.event_type,
        is_active=row.is_active,
    )


def _sync_run_read(row: IntegrationSyncRun) -> SyncRunRead:
    return SyncRunRead(
        id=row.id,
        connection_id=row.connection_id,
        trigger=row.trigger,
        event_type=row.event_type,
        status=row.status,
        attempts=row.attempts,
        items_processed=row.items_processed,
        error=row.error,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
    )


__all__ = [
    "FEATURE",
    "MANAGE_PERMISSION",
    "IntegrationService",
    "IntegrationSyncService",
    "to_connection_read",
]
