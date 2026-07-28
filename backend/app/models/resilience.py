"""Business continuity & operational resilience models (Phase 8.6).

Five tenant-scoped, RLS-FORCEd tables:

  * ``BusinessService`` — the service registry entry: a service the business
    runs, its criticality tier, owner, and recovery objectives (RTO/RPO targets).
  * ``ServiceDependency`` — a directed ``service -> depends_on`` edge, the
    dependency registry a blast-radius question is answered from.
  * ``ContinuityPlan`` — a business-continuity or disaster-recovery plan, its
    status, objectives, steps, and last-tested date.
  * ``OperationalIncident`` — one operational incident: its severity, lifecycle,
    the service it affected, timestamps, and the recovery breach flags computed
    at resolution.
  * ``PostIncidentReview`` — the post-incident review attached one-to-one to an
    incident: root cause, action items, and lessons learned.

The dashboard reuses tenant health, the trust rating, and the governance
sensitive-asset count; incidents emit through the existing metrics registry. No
monitoring or observability pipeline is restated here.
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


class BusinessService(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "business_services"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()
    owner_id: Mapped[UUID | None] = _actor_fk()

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: low | medium | high | critical.
    criticality: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="medium"
    )
    #: Recovery objectives, in minutes. Null means "no target set".
    rto_target_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rpo_target_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
        UniqueConstraint("organization_id", "name", name="uq_business_services_org_name"),
        Index("ix_business_services_org", "organization_id", "criticality"),
    )


class ServiceDependency(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "service_dependencies"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()

    service_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("business_services.id", ondelete="CASCADE"),
        nullable=False,
    )
    depends_on_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("business_services.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: hard (the service cannot function without it) | soft (degraded).
    dependency_type: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="hard"
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

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
            "organization_id", "service_id", "depends_on_id",
            name="uq_service_dependencies_pair",
        ),
        Index("ix_service_dependencies_org_service", "organization_id", "service_id"),
        CheckConstraint(
            "service_id <> depends_on_id",
            name="ck_service_dependencies_no_self",
        ),
    )


class ContinuityPlan(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "continuity_plans"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()
    owner_id: Mapped[UUID | None] = _actor_fk()
    #: The service this plan covers, when it is service-specific. SET NULL so
    #: retiring a service does not delete its recovery documentation.
    service_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("business_services.id", ondelete="SET NULL"),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    #: business_continuity | disaster_recovery.
    plan_type: Mapped[str] = mapped_column(String(24), nullable=False)
    #: draft | active | archived.
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="draft"
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    rto_target_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rpo_target_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Ordered recovery steps: a list of {order, action, owner}.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )

    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_review_at: Mapped[date | None] = mapped_column(Date, nullable=True)

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
        Index("ix_continuity_plans_org", "organization_id", "plan_type"),
        Index("ix_continuity_plans_org_service", "organization_id", "service_id"),
    )


class OperationalIncident(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "operational_incidents"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()
    #: The incident commander, when assigned.
    commander_id: Mapped[UUID | None] = _actor_fk()
    service_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("business_services.id", ondelete="SET NULL"),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: low | medium | high | critical.
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    #: open | investigating | identified | monitoring | resolved.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="open"
    )
    impact_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Computed at resolution: minutes from start to resolution, and the
    #: measured data-loss window for the RPO check.
    recovery_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_loss_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Set at resolution against the affected service's targets. Null when there
    #: was no target or nothing measured — neither met nor breached.
    rto_breached: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rpo_breached: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

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
        Index("ix_operational_incidents_org_status", "organization_id", "status"),
        Index("ix_operational_incidents_org_started", "organization_id", "started_at"),
    )


class PostIncidentReview(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "post_incident_reviews"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()
    reviewed_by: Mapped[UUID | None] = _actor_fk()
    #: One review per incident.
    incident_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("operational_incidents.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: draft | completed.
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="draft"
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    contributing_factors: Mapped[str | None] = mapped_column(Text, nullable=True)
    lessons_learned: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Follow-up actions: a list of {action, owner, due, done}.
    action_items: Mapped[list[dict[str, Any]]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )

    completed_at: Mapped[datetime | None] = mapped_column(
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
            "organization_id", "incident_id", name="uq_post_incident_reviews_incident"
        ),
    )
