"""Compliance operations models (Phase 8.3).

Two tenant-scoped, RLS-FORCEd tables:

  * ``DataProcessingActivity`` — a GDPR Article 30 record of processing: what
    data is processed, why, on what lawful basis, for whom, and with what
    safeguards. One row per activity.
  * ``ComplianceEvidence`` — an artefact attached to a control key, so a
    procedural control that no database flag can prove is satisfied by a recorded
    piece of evidence (a policy document, a screenshot, an attestation).

The DSAR workflow and retention execution reuse the existing Phase 8.0
``DataRequest`` pipeline and ``RetentionService`` rather than new tables.
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
    String,
    Text,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class DataProcessingActivity(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "data_processing_activities"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    #: The Article 6 lawful basis, e.g. consent | contract | legitimate_interests.
    lawful_basis: Mapped[str] = mapped_column(String(60), nullable=False)

    data_categories: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    data_subjects: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    recipients: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )

    retention_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Whether the activity involves a transfer outside the primary jurisdiction.
    cross_border: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    safeguards: Mapped[str | None] = mapped_column(String(500), nullable=True)
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
        Index("ix_data_processing_activities_org", "organization_id", "created_at"),
    )


class ComplianceEvidence(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "compliance_evidence"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    collected_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: The control this evidence attests to (a registry key).
    control_key: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: A pointer to the artefact (a document URL, a ticket) — not the artefact.
    reference_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )

    collected_at: Mapped[datetime] = mapped_column(
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
        Index("ix_compliance_evidence_org", "organization_id"),
        Index("ix_compliance_evidence_control", "organization_id", "control_key"),
    )
