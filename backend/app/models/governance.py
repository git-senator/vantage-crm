"""Data governance & advanced privacy models (Phase 8.5).

Three tenant-scoped, RLS-FORCEd tables:

  * ``DataAsset`` — one catalog entry: a dataset, table, field, report, or stream
    the tenant governs, with its classification level, privacy labels, owner and
    steward, retention hint, and an optional link to the compliance record of
    processing it belongs to. The sensitive-data registry is a query over this
    table, not a second table.
  * ``DataQualityRule`` — a quality assertion on an asset: a dimension, a
    threshold, and the last measured value and status. The rules framework grades
    a measurement; the row remembers the latest grade.
  * ``DataLineageEdge`` — a directed upstream -> downstream relationship between
    two assets, the lineage metadata a change-impact question is answered from.

The RoPA link reuses the Phase 8.3 ``data_processing_activities`` table; the trust
rating on the dashboard reuses the Phase 8.4 Trust Center. No GDPR, compliance,
or classification-of-processing logic is restated here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
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


class DataAsset(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "data_assets"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()
    #: The business owner accountable for the asset, and the steward who curates
    #: it. SET NULL so a departure leaves the asset unowned (a surfaced gap)
    #: rather than deleting it.
    owner_id: Mapped[UUID | None] = _actor_fk()
    steward_id: Mapped[UUID | None] = _actor_fk()

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: dataset | table | field | report | stream.
    asset_type: Mapped[str] = mapped_column(String(30), nullable=False)
    #: The source system the asset lives in (e.g. "crm", "warehouse").
    system: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: A classification level from the framework. Stored, not derived on read, so
    #: an explicit override survives; the service recommends and defaults it.
    classification: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="internal"
    )
    privacy_labels: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    #: Derived from the labels at write time, stored for cheap filtering.
    contains_pii: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: Policy metadata: the lawful basis and retention hint that travel with the
    #: asset. These annotate; the authoritative retention windows live in the
    #: compliance policy, and the RoPA link points at the processing record.
    lawful_basis: Mapped[str | None] = mapped_column(String(60), nullable=True)
    retention_hint: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cross_border: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: Optional link to the compliance record of processing this asset feeds.
    processing_activity_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("data_processing_activities.id", ondelete="SET NULL"),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
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
        UniqueConstraint("organization_id", "name", name="uq_data_assets_org_name"),
        Index("ix_data_assets_org_created", "organization_id", "created_at"),
        Index("ix_data_assets_org_classification", "organization_id", "classification"),
    )


class DataQualityRule(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "data_quality_rules"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()
    asset_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("data_assets.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: A dimension from the quality framework.
    dimension: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The percentage a measurement must reach to pass.
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    #: A mandatory rule failing fails the asset; an advisory one warns.
    mandatory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    #: The latest measurement and its grade. Null until first measured.
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
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
        Index("ix_data_quality_rules_org_asset", "organization_id", "asset_id"),
        CheckConstraint(
            "threshold >= 0 AND threshold <= 100",
            name="ck_data_quality_rules_threshold",
        ),
    )


class DataLineageEdge(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "data_lineage_edges"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()

    upstream_asset_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("data_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    downstream_asset_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("data_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: What the transformation between the two assets does.
    transformation: Mapped[str | None] = mapped_column(String(500), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
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
            "organization_id", "upstream_asset_id", "downstream_asset_id",
            name="uq_data_lineage_edges_pair",
        ),
        Index("ix_data_lineage_edges_org_up", "organization_id", "upstream_asset_id"),
        Index(
            "ix_data_lineage_edges_org_down",
            "organization_id", "downstream_asset_id",
        ),
        CheckConstraint(
            "upstream_asset_id <> downstream_asset_id",
            name="ck_data_lineage_edges_no_self_loop",
        ),
    )


__all__ = ["DataAsset", "DataLineageEdge", "DataQualityRule"]
