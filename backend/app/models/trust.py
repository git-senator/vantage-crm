"""Trust & risk management models (Phase 8.4).

Four tenant-scoped, RLS-FORCEd tables:

  * ``Risk`` — one entry in the enterprise risk register: a scored exposure with
    an owner, a treatment, a remediation plan, and a review cadence. Both the
    inherent score (before treatment) and the residual score (after) are stored,
    because the residual is what drives the trust rating.
  * ``Certification`` — a tracked compliance attestation (SOC 2, ISO 27001, …),
    its status and validity window. A lapsed one is a finding.
  * ``TrustProfile`` — the singleton Trust Center record for the tenant: the
    customer-facing headline, contact, and subprocessor list, plus whether it is
    published. One row per organization.
  * ``QuestionnaireItem`` — a reusable security-questionnaire Q&A the tenant
    maintains once and answers vendor reviews from; a `is_public` item surfaces
    on the customer-facing overview.

The posture aggregation reuses the Phase 8.2 security and Phase 8.3 compliance
signals rather than recomputing them — no security or compliance state is
duplicated here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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


def _org_fk() -> Mapped[UUID]:
    return mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )


def _actor_fk() -> Mapped[UUID | None]:
    return mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class Risk(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "risks"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()
    #: The account accountable for treating the risk. SET NULL so a departing
    #: owner does not take the register entry with them — it becomes unowned,
    #: which is exactly the state that needs surfacing.
    owner_id: Mapped[UUID | None] = _actor_fk()

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(60), nullable=False)

    #: Inherent risk, scored 1-5 on each axis; product banded to a level.
    likelihood: Mapped[int] = mapped_column(Integer, nullable=False)
    impact: Mapped[int] = mapped_column(Integer, nullable=False)
    inherent_score: Mapped[int] = mapped_column(Integer, nullable=False)
    inherent_level: Mapped[str] = mapped_column(String(10), nullable=False)

    #: How the tenant has chosen to treat it, and the residual after treatment.
    treatment: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="mitigate"
    )
    residual_likelihood: Mapped[int] = mapped_column(Integer, nullable=False)
    residual_impact: Mapped[int] = mapped_column(Integer, nullable=False)
    residual_score: Mapped[int] = mapped_column(Integer, nullable=False)
    residual_level: Mapped[str] = mapped_column(String(10), nullable=False)

    #: open | assessing | mitigating | monitoring | accepted | closed.
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="open"
    )
    remediation_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
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
        Index("ix_risks_org_created", "organization_id", "created_at"),
        Index("ix_risks_org_status", "organization_id", "status"),
        CheckConstraint(
            "likelihood BETWEEN 1 AND 5 AND impact BETWEEN 1 AND 5",
            name="ck_risks_inherent_axes",
        ),
        CheckConstraint(
            "residual_likelihood BETWEEN 1 AND 5 "
            "AND residual_impact BETWEEN 1 AND 5",
            name="ck_risks_residual_axes",
        ),
    )


class Certification(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "certifications"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()

    #: A registered certification-framework key.
    framework: Mapped[str] = mapped_column(String(40), nullable=False)
    #: not_started | in_progress | certified | expired.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="in_progress"
    )
    auditor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reference_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    issued_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)

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
            "organization_id", "framework", name="uq_certifications_org_framework"
        ),
        Index("ix_certifications_org", "organization_id"),
    )


class TrustProfile(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "trust_profiles"

    organization_id: Mapped[UUID] = _org_fk()

    headline: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    security_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: A pointer to the tenant's public security page or policy, not the page.
    policy_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    #: Declared subprocessors: a list of {name, purpose, location}. Kept as JSONB
    #: rather than a table — it is a small, wholly-replaced list, and it is only
    #: ever read back as a block for the overview.
    subprocessors: Mapped[list[dict[str, Any]]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )

    #: Whether the customer-facing overview is published. Publishing is an egress
    #: decision — it exposes the profile beyond the workspace.
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    published_at: Mapped[datetime | None] = mapped_column(
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
        UniqueConstraint("organization_id", name="uq_trust_profiles_org"),
    )


class QuestionnaireItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "questionnaire_items"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()

    category: Mapped[str] = mapped_column(String(60), nullable=False)
    question: Mapped[str] = mapped_column(String(500), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    #: A public item surfaces on the customer-facing overview; a private one is
    #: internal reference an analyst answers a review from.
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
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
        Index("ix_questionnaire_items_org", "organization_id", "sort_order"),
    )
