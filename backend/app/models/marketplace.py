"""Integration marketplace model (Phase 9.2).

One table: ``IntegrationListing`` — the marketplace registry entry. It uses the
same *operational* RLS policy the plugin catalog uses: a row with a NULL
``publisher_organization_id`` is a curated listing the platform ships, visible to
every tenant; a row with a publisher is that tenant's private listing. The keying
column and the policy are identical to ``plugins`` on purpose — a listing is the
marketplace face of a plugin, so it shares the plugin's visibility model.

A listing carries its marketplace metadata (vendor, rich category, certification,
how it authenticates) plus the plugin ``manifest`` that provisions it, so the
installation workflow can stand a plugin up from a listing without a second source
of truth. It restates none of the plugin platform: the installation, its
lifecycle, its secrets and its event subscriptions all live on the plugin tables;
the listing only points at them by key.
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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class IntegrationListing(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "integration_listings"

    #: NULL = curated, globally visible. Non-NULL = a tenant's private listing.
    #: The operational RLS policy keys on this column, exactly as ``plugins`` does.
    publisher_organization_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Globally unique, and identical to the key of the plugin it provisions.
    key: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    vendor: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The rich marketplace category (see ``app.marketplace.categories``).
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    #: How the integration authenticates: oauth2 | api_key | webhook | none.
    auth_method: Mapped[str] = mapped_column(String(16), nullable=False)
    oauth_scopes: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    #: An optional link to a live Phase 7.7 integration provider.
    provider_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: The certification tier (see ``app.marketplace.certification``).
    certification: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="community"
    )

    #: The plugin manifest that provisions this listing, kept verbatim.
    manifest: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )
    #: Denormalised from the manifest for discovery display.
    capabilities: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    event_types: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    required_feature: Mapped[str | None] = mapped_column(String(100), nullable=True)
    docs_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_first_party: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: listed | deprecated.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="listed"
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
        UniqueConstraint("key", name="uq_integration_listings_key"),
        Index("ix_integration_listings_publisher", "publisher_organization_id"),
        Index("ix_integration_listings_category", "category"),
        Index("ix_integration_listings_certification", "certification"),
    )

    def __repr__(self) -> str:
        return f"<IntegrationListing {self.key!r} {self.certification}>"
