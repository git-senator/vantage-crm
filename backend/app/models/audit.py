"""Append-only audit log.

Deliberately separate from the (future) `activities` table. They look similar
and are constantly conflated, but they differ in every way that matters:

                  activities                  audit_logs
    audience      agents, in the timeline UI   security and compliance
    content       "logged a call with Harper"  field-level before/after, IP
    mutable       yes (edit a note)            never
    retention     business lifetime            compliance-defined

Immutability is enforced at the grant level, not in application code: the
application role holds INSERT and SELECT and nothing else, so an application
bug — or an attacker with the app's credentials — cannot rewrite history.

`actor_email` is denormalised on purpose. A foreign key alone would leave the
log unreadable after a user is deleted, which is exactly when it is needed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_logs"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # SET NULL rather than CASCADE: deleting a user must not delete the record
    # of what they did.
    actor_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Survives actor deletion, which is when the log matters most.
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    action: Mapped[str] = mapped_column(String(60), nullable=False)

    #: Polymorphic target. NULL for actions with no entity (e.g. login).
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )

    #: {field: {old, new}} for updates; free-form context otherwise.
    #: Secrets are redacted before they reach here.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )

    ip_address: Mapped[str | None] = mapped_column(postgresql.INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    #: Ties an audit entry to the request's log lines.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # The two access patterns: an org's recent history, and one record's.
        Index("ix_audit_logs_org_created", "organization_id", "created_at"),
        Index("ix_audit_logs_entity", "organization_id", "entity_type", "entity_id"),
        Index("ix_audit_logs_actor", "organization_id", "actor_id", "created_at"),
        Index("ix_audit_logs_action", "organization_id", "action", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.entity_type}:{self.entity_id}>"
