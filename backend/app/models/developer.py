"""Marketplace developer platform models (Phase 9.5).

Four tenant-scoped, RLS-FORCEd tables that turn the marketplace into a
developer-facing application platform:

  * ``DeveloperOrganization`` — a tenant's developer identity: who publishes
    applications, and whether they are in good standing.
  * ``MarketplaceApplication`` — an application a developer authors, wrapping a
    plugin, moving through the publication lifecycle.
  * ``ApplicationVersionReview`` — the review history behind that lifecycle: the
    decision, the reviewer, and the notes, per application version.
  * ``DeveloperApiCredential`` — a developer's scoped API credential. Only a
    SHA-256 hash of the secret is stored; the raw value is shown once at creation
    and never again, exactly as ``api_keys`` does.

Every table carries ``organization_id`` and is isolated by the tenant policy — a
developer identity belongs to the workspace that created it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
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


class DeveloperOrganization(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "developer_organizations"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: active | suspended.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
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

    __table_args__ = (Index("ix_developer_organizations_org", "organization_id"),)

    def __repr__(self) -> str:
        return f"<DeveloperOrganization {self.id} {self.name!r} {self.status}>"


class MarketplaceApplication(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "marketplace_applications"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()
    developer_org_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("developer_organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The plugin this application wraps, once provisioned. SET NULL keeps the
    #: application row if the plugin is removed.
    plugin_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("plugins.id", ondelete="SET NULL"),
        nullable=True,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), nullable=False)
    #: display_name, summary, category, homepage, version, etc.
    app_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", postgresql.JSONB, nullable=False, server_default="{}"
    )
    #: draft | submitted | review | approved | published | deprecated | retired.
    lifecycle_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="draft"
    )

    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
        UniqueConstraint(
            "developer_org_id", "slug", name="uq_marketplace_applications_slug"
        ),
        Index(
            "ix_marketplace_applications_org_dev",
            "organization_id", "developer_org_id",
        ),
        Index(
            "ix_marketplace_applications_status",
            "organization_id", "lifecycle_status",
        ),
    )

    def __repr__(self) -> str:
        return f"<MarketplaceApplication {self.slug} {self.lifecycle_status}>"


class ApplicationVersionReview(Base, UUIDPrimaryKeyMixin):
    """A review decision on a version of an application.

    Keyed to ``(application_id, version)`` rather than a separate version table —
    the application already carries its current version in metadata, and the
    review is the durable governance record of a decision made about it.
    """

    __tablename__ = "application_version_reviews"

    organization_id: Mapped[UUID] = _org_fk()
    application_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("marketplace_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer_id: Mapped[UUID | None] = _actor_fk()

    version: Mapped[str] = mapped_column(String(20), nullable=False)
    #: approved | rejected.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_application_version_reviews_app",
            "organization_id", "application_id",
        ),
    )

    def __repr__(self) -> str:
        return f"<ApplicationVersionReview {self.application_id} {self.status}>"


class DeveloperApiCredential(Base, UUIDPrimaryKeyMixin):
    """A developer's scoped API credential. Only the SHA-256 hash is stored."""

    __tablename__ = "developer_api_credentials"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()
    developer_org_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("developer_organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: SHA-256 of the raw secret. The raw value exists only in the creation
    #: response — a database disclosure yields no usable credentials.
    hashed_secret: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: Public identifier shown in listings (prefix + first chars of the secret).
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    last_four: Mapped[str] = mapped_column(String(8), nullable=False)

    scopes: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_developer_api_credentials_dev",
            "organization_id", "developer_org_id",
        ),
    )

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __repr__(self) -> str:
        # Never include the hash.
        return f"<DeveloperApiCredential {self.id} {self.prefix}...{self.last_four}>"
