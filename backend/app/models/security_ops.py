"""Security operations models (Phase 8.2).

Three tenant-scoped, RLS-FORCEd tables:

  * ``SecurityEvent`` — an append-only record of one security-relevant
    occurrence (a sign-in, an MFA failure, a new device). Immutable, so it has no
    ``updated_at``.
  * ``SecurityAlert`` — a tracked finding raised by the detection framework, with
    a lifecycle (open → acknowledged → resolved/dismissed) and a ``dedup_key`` so
    repeated detections of the same thing fold into one alert with a rising
    ``occurrences`` count rather than a wall of duplicates.
  * ``TrustedDevice`` — the device/session intelligence: which devices an account
    has used, when, and whether the account owner has marked them trusted. The
    first sighting of a fingerprint is what "new device" means.
"""

from __future__ import annotations

from datetime import datetime
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


class SecurityEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "security_events"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: The account the event concerns, when there is one. SET NULL so a deleted
    #: user does not take the security history with them.
    user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)

    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    device_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_score: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_security_events_org_created", "organization_id", "created_at"),
        Index("ix_security_events_org_user", "organization_id", "user_id"),
        Index("ix_security_events_org_type", "organization_id", "event_type"),
    )


class SecurityAlert(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "security_alerts"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    category: Mapped[str] = mapped_column(String(30), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    #: open | acknowledged | resolved | dismissed.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="open"
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    subject_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    risk_score: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    #: Folds repeated detections of the same thing into one open alert.
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)
    occurrences: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    acknowledged_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
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
        Index("ix_security_alerts_org_status", "organization_id", "status", "last_seen_at"),
        Index("ix_security_alerts_org_dedup", "organization_id", "dedup_key"),
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'dismissed')",
            name="ck_security_alerts_status",
        ),
    )


class TrustedDevice(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "trusted_devices"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    device_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    last_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    last_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    #: The account owner has vouched for this device.
    trusted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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
            "organization_id", "user_id", "device_fingerprint",
            name="uq_trusted_devices_org_user_fingerprint",
        ),
        Index("ix_trusted_devices_org_user", "organization_id", "user_id"),
    )
