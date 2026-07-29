"""Marketplace SDK & access models (Phase 9.6).

Three tenant-scoped, RLS-FORCEd tables that record what a marketplace application
declares and is granted:

  * ``MarketplaceSdkApplication`` — an application's SDK requirements: the version
    it targets and the capabilities it requests.
  * ``MarketplaceApiAccessGrant`` — a tenant's grant of capabilities to an
    application. An active grant is one with a NULL ``revoked_at``.
  * ``MarketplaceEventSubscription`` — foundation: an application's interest in a
    platform event.

Every table carries ``organization_id`` and is isolated by the tenant policy. No
secret is stored on any of them — access is a grant of capabilities, not a
credential.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
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


def _application_fk() -> Mapped[UUID]:
    return mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("marketplace_applications.id", ondelete="CASCADE"),
        nullable=False,
    )


class MarketplaceSdkApplication(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "marketplace_sdk_applications"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()
    application_id: Mapped[UUID] = _application_fk()

    sdk_version: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The SDK capabilities the application declares it needs.
    capabilities: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    #: The RBAC permissions those capabilities resolve to (denormalised).
    requested_permissions: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    #: registered (compatible and recorded).
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="registered"
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
        UniqueConstraint("application_id", name="uq_marketplace_sdk_applications"),
        Index("ix_marketplace_sdk_applications_org", "organization_id"),
    )

    def __repr__(self) -> str:
        return f"<MarketplaceSdkApplication {self.application_id} v{self.sdk_version}>"


class MarketplaceApiAccessGrant(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "marketplace_api_access_grants"

    #: The tenant granting access — the RLS key.
    organization_id: Mapped[UUID] = _org_fk()
    application_id: Mapped[UUID] = _application_fk()
    granted_by: Mapped[UUID | None] = _actor_fk()

    #: The SDK capabilities granted to the application.
    granted_permissions: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
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
        Index(
            "ix_marketplace_api_access_grants_app",
            "organization_id", "application_id", "revoked_at",
        ),
    )

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __repr__(self) -> str:
        state = "active" if self.is_active else "revoked"
        return f"<MarketplaceApiAccessGrant {self.application_id} {state}>"


class MarketplaceEventSubscription(Base, UUIDPrimaryKeyMixin):
    """Foundation: an application's interest in a platform event."""

    __tablename__ = "marketplace_event_subscriptions"

    organization_id: Mapped[UUID] = _org_fk()
    application_id: Mapped[UUID] = _application_fk()

    event_name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "application_id", "event_name",
            name="uq_marketplace_event_subscriptions",
        ),
        Index(
            "ix_marketplace_event_subscriptions_app",
            "organization_id", "application_id",
        ),
    )

    def __repr__(self) -> str:
        return f"<MarketplaceEventSubscription {self.application_id} {self.event_name}>"
