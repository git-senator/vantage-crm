"""Webhooks — outbound event delivery to a tenant's own URL (Phase 7.3).

Two tables, mirroring the split the automation outbox already makes between
*subscription* and *work*:

  * ``WebhookEndpoint`` is the durable subscription — a URL, the events it wants,
    and the HMAC secret used to sign deliveries. The secret is stored encrypted
    at rest through the same ``SecretBox`` that seals MFA secrets, and the
    plaintext is shown once at creation/rotation, exactly like an API key's raw
    value.
  * ``WebhookDelivery`` is one attempt-bearing record of "this event, to this
    endpoint" — its payload, status, attempt count and the last response. It is
    the delivery history, and the ``(endpoint_id, event_id)`` uniqueness is what
    makes dispatch idempotent: re-dispatching an event (the sweep, a duplicate
    enqueue) can never fan out a second copy to the same endpoint.

Both are tenant-scoped and RLS-FORCEd. Neither needs a SECURITY DEFINER lookup —
unlike an API key or a refresh token, a webhook is only ever read inside an
already-bound tenant context (management runs under the caller's org; the
dispatch and delivery jobs bind the tenant first), so the opaque-credential
dance does not apply.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
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


class WebhookEndpoint(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "webhook_endpoints"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # The user who created the endpoint: audit actor. SET NULL rather than
    # CASCADE — deleting a user must not silently drop a live integration.
    created_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: A human label so an operator can tell endpoints apart ("Zapier", "billing
    #: sync").
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    #: Where deliveries are POSTed. https is required at the schema layer.
    url: Mapped[str] = mapped_column(String(2048), nullable=False)

    # HMAC-SHA256 signing secret, encrypted at rest (SecretBox). The plaintext
    # is returned once on create/rotate and never again.
    secret: Mapped[str] = mapped_column(Text, nullable=False)

    #: The event types this endpoint subscribes to, e.g. ["lead.created"].
    event_types: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    #: Set when auto-disabled after too many consecutive failures, so the owner
    #: can tell a deliberate pause from a broken receiver.
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Consecutive failed deliveries. Reset to zero on any success; when it
    #: reaches the configured ceiling the endpoint is disabled.
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
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

    __table_args__ = (Index("ix_webhook_endpoints_org", "organization_id"),)

    def __repr__(self) -> str:
        # Never include the secret.
        return f"<WebhookEndpoint {self.id} {self.name!r}>"


class WebhookDelivery(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "webhook_deliveries"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    endpoint_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: The outbox event this delivery carries. Part of the idempotency key.
    event_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)

    #: The exact JSON body that is (or was) signed and sent.
    payload: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )

    #: pending | succeeded | failed | exhausted.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: A truncated snippet of the last response body, for diagnosis.
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: A transport-level error (timeout, refused) when there was no response.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: When the next retry becomes due. The sweep re-enqueues past-due pending
    #: rows, so a lost delivery job is a delay, not a dropped event.
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
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
        UniqueConstraint(
            "endpoint_id", "event_id", name="uq_webhook_deliveries_endpoint_event"
        ),
        Index("ix_webhook_deliveries_org", "organization_id"),
        Index("ix_webhook_deliveries_endpoint", "endpoint_id", "created_at"),
        # The sweep predicate: past-due, still-pending deliveries.
        Index("ix_webhook_deliveries_retry", "status", "next_attempt_at"),
    )

    def __repr__(self) -> str:
        return f"<WebhookDelivery {self.id} {self.event_type} {self.status}>"
