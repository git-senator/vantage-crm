"""Declarative base and shared column mixins.

`TenantMixin` is the mechanism that makes decision D1 real: because every
business model inherits it, `organization_id` cannot be forgotten on a new
table. Multi-tenancy is structural rather than a convention someone has to
remember at 5pm on a Friday.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from uuid6 import uuid7


class Base(DeclarativeBase):
    """Shared declarative base. Metadata is what Alembic autogenerates from."""


class UUIDPrimaryKeyMixin:
    """UUIDv7 primary key.

    v7 rather than v4 because it is time-ordered: inserts land at the end of the
    B-tree instead of scattering across it, which keeps index bloat and write
    amplification down at scale. Still non-enumerable, unlike a serial.
    """

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), primary_key=True, default=uuid7
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """CRM users expect undo, and retention rules expect the row to survive."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class TenantMixin:
    """Attaches `organization_id` and its index to every business table."""

    @declared_attr
    @classmethod
    def organization_id(cls) -> Mapped[UUID]:
        return mapped_column(
            postgresql.UUID(as_uuid=True),
            ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )

    @declared_attr.directive
    @classmethod
    def __table_args__(cls) -> tuple[Index, ...]:
        # Tenant-first composite index: nearly every query filters on the org
        # before anything else, so it belongs at the front of the index.
        return (
            Index(
                f"ix_{cls.__tablename__}_org_created",  # type: ignore[attr-defined]
                "organization_id",
                "created_at",
            ),
        )


class AuditedMixin:
    """Who created and last touched the row."""

    @declared_attr
    @classmethod
    def created_by(cls) -> Mapped[UUID | None]:
        return mapped_column(
            postgresql.UUID(as_uuid=True),
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        )

    @declared_attr
    @classmethod
    def updated_by(cls) -> Mapped[UUID | None]:
        return mapped_column(
            postgresql.UUID(as_uuid=True),
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        )
