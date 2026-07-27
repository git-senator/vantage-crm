"""Integrations — installed external connections, their subscriptions, and syncs.

Phase 7.7. Three tenant-scoped, RLS-FORCEd tables, mirroring the split the
webhook and automation subsystems already make between a durable *subscription*
and the *work* it produces:

  * ``IntegrationConnection`` is the installed integration: which provider, which
    external account, the OAuth tokens (encrypted at rest through the shared
    ``SecretBox``), and its health. The tokens are never returned by any read
    projection — they exist only to be handed to a provider at sync time.
  * ``IntegrationSubscription`` is a connection's interest in a CRM event type,
    so a change in the CRM can drive an outbound sync. Unique on
    ``(connection_id, event_type)`` so subscribing is idempotent.
  * ``IntegrationSyncRun`` is one attempt-bearing record of a sync — its trigger,
    status, item count, cursor and error. It is the delivery history, the health
    signal, and the retry ledger, all on the row so a lost job is latency rather
    than a dropped sync.

None needs a SECURITY DEFINER lookup: an integration is only ever read inside an
already-bound tenant context (management under the caller's org; the jobs bind
the tenant first), unlike an API key whose tenant must be resolved from an opaque
credential.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class IntegrationConnection(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "integration_connections"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # The user who installed it: audit actor and the recipient of health alerts.
    created_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: The provider key, e.g. "google_calendar". Matches a registry provider.
    provider: Mapped[str] = mapped_column(String(50), nullable=False)

    #: pending | active | error | disconnected.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )

    #: The connected external account, for display ("which Google account").
    external_account_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    external_account_email: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    #: The OAuth scopes actually granted.
    scopes: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )

    # OAuth tokens, encrypted at rest (SecretBox). Never in a read projection.
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: The random state minted at install and echoed back on the OAuth callback,
    #: binding a completion to the connection that began it.
    oauth_state: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Small provider-specific state (a resume cursor, a calendar id).
    config: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )

    #: unknown | healthy | degraded | down, derived from recent sync outcomes.
    health: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="unknown"
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Consecutive failed syncs; reset on success. At the ceiling the connection
    #: is auto-disabled (status → error) rather than retried forever.
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_integration_connections_org", "organization_id"),
        Index("ix_integration_connections_provider", "organization_id", "provider"),
        CheckConstraint(
            "status IN ('pending', 'active', 'error', 'disconnected')",
            name="ck_integration_connections_status",
        ),
    )

    @property
    def is_active(self) -> bool:
        return self.status == "active" and self.disabled_at is None

    def token_is_expired(self, *, now: datetime | None = None, skew_seconds: int = 60) -> bool:
        """Whether the access token needs refreshing before use.

        A skew treats a token that expires within the next minute as already
        expired, so a sync does not start a network call with a token that dies
        mid-flight.
        """
        if self.token_expires_at is None:
            return False
        moment = now or datetime.now(UTC)
        expiry = self.token_expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        return (expiry - moment).total_seconds() <= skew_seconds

    def __repr__(self) -> str:
        # Never include the tokens.
        return f"<IntegrationConnection {self.id} {self.provider} {self.status}>"


class IntegrationSubscription(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "integration_subscriptions"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    connection_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("integration_connections.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: A CRM outbox event type this connection reacts to.
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "connection_id", "event_type", name="uq_integration_subscriptions"
        ),
        Index("ix_integration_subscriptions_org", "organization_id"),
        Index("ix_integration_subscriptions_event", "organization_id", "event_type"),
    )

    def __repr__(self) -> str:
        return f"<IntegrationSubscription {self.connection_id} {self.event_type}>"


class IntegrationSyncRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "integration_sync_runs"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    connection_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("integration_connections.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: scheduled | manual | event.
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The CRM event that triggered this run, when trigger == 'event'.
    event_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    #: pending | running | succeeded | failed.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    items_processed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    #: The resume token the run produced, kept for diagnosis (the authoritative
    #: copy lives on the connection's config).
    cursor: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_integration_sync_runs_org", "organization_id"),
        Index(
            "ix_integration_sync_runs_connection", "connection_id", "created_at"
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_integration_sync_runs_status",
        ),
    )

    def __repr__(self) -> str:
        return f"<IntegrationSyncRun {self.id} {self.trigger} {self.status}>"
