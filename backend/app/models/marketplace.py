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
    Integer,
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
    #: The publication lifecycle state (Phase 9.3): draft | review | approved |
    #: published | deprecated | retired. Curated listings are synced straight to
    #: ``published``; tenant-authored listings travel the full path.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="draft"
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


class IntegrationVersion(Base, UUIDPrimaryKeyMixin):
    """A published version of a listing (Phase 9.3).

    Each version keeps the manifest it was cut from and its compatibility
    metadata, so an install records exactly which version it took and an upgrade
    can compare against the latest. It shares the listing's *operational* RLS
    visibility (NULL publisher = curated/global, else the tenant's own) via the
    same keying column.
    """

    __tablename__ = "integration_versions"

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
    listing_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("integration_listings.id", ondelete="CASCADE"),
        nullable=False,
    )

    version: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The plugin manifest this version was cut from.
    manifest: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )
    #: Compatibility metadata, e.g. the SDK version this cut targets.
    compatibility: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )
    #: draft | published | deprecated.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="published"
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
        UniqueConstraint("listing_id", "version", name="uq_integration_versions"),
        Index("ix_integration_versions_publisher", "publisher_organization_id"),
        Index("ix_integration_versions_listing", "listing_id"),
    )

    def __repr__(self) -> str:
        return f"<IntegrationVersion {self.listing_id} v{self.version} {self.status}>"


class IntegrationInstallation(Base, UUIDPrimaryKeyMixin):
    """A tenant's operational record of an installed integration (Phase 9.3).

    The runtime truth lives on ``plugin_installations``; this row is the
    marketplace's operational tracking of it — which listing, which version, and
    the install-history status — so a workspace can answer "what did we install,
    at what version, and when did we remove it?" without mining the runtime.
    Tenant-scoped and RLS-FORCEd.
    """

    __tablename__ = "integration_installations"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    installed_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    listing_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("integration_listings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: The catalog plugin the listing provisioned. SET NULL keeps history if the
    #: plugin row is ever removed.
    installed_plugin_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("plugins.id", ondelete="SET NULL"),
        nullable=True,
    )
    installed_version: Mapped[str] = mapped_column(String(20), nullable=False)

    #: pending | active | upgrading | uninstalled | failed.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )

    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    uninstalled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_integration_installations_org_status",
            "organization_id", "status",
        ),
        Index(
            "ix_integration_installations_org_listing",
            "organization_id", "listing_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<IntegrationInstallation {self.organization_id} "
            f"{self.listing_id} {self.status}>"
        )


class IntegrationReview(Base, UUIDPrimaryKeyMixin):
    """A review decision on a tenant-authored listing (Phase 9.3).

    The governance record behind the publication path: who reviewed the listing,
    what they decided, and the evidence for it. Tenant-scoped to the authoring
    workspace — a listing is reviewed inside the org that owns it.
    """

    __tablename__ = "integration_reviews"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    listing_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("integration_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: approved | rejected.
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_integration_reviews_org_listing", "organization_id", "listing_id"
        ),
    )

    def __repr__(self) -> str:
        return f"<IntegrationReview {self.listing_id} {self.decision}>"


class IntegrationPlan(Base, UUIDPrimaryKeyMixin):
    """A pricing plan for a listing (Phase 9.4).

    A listing may have several plans (a free tier and a paid tier, say). Money is
    integer cents. Shares the listing's *operational* RLS visibility via the same
    keying column, so a curated listing's plans are global and a tenant's private
    listing's plans are the tenant's.
    """

    __tablename__ = "integration_plans"

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
    listing_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("integration_listings.id", ondelete="CASCADE"),
        nullable=False,
    )

    key: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: free | flat | per_seat | usage.
    pricing_model: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    #: month | year.
    interval: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="month"
    )
    included_units: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    unit_amount_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

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
        UniqueConstraint("listing_id", "key", name="uq_integration_plans"),
        Index("ix_integration_plans_publisher", "publisher_organization_id"),
        Index("ix_integration_plans_listing", "listing_id"),
    )

    def __repr__(self) -> str:
        return f"<IntegrationPlan {self.listing_id} {self.key} {self.pricing_model}>"


class IntegrationEntitlement(Base, UUIDPrimaryKeyMixin):
    """A tenant's right to a paid integration (Phase 9.4).

    Tenant-scoped and RLS-FORCEd. Records the plan, the entitlement status, the
    trial and period deadlines the effective status is derived from, and an opaque
    ``provider_reference`` — the seam where the existing billing provider
    abstraction would place its handle. No payment is processed here.
    """

    __tablename__ = "integration_entitlements"

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
    listing_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("integration_listings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    plan_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("integration_plans.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: trialing | active | canceled | expired.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The billing provider that backs this entitlement (label only).
    provider: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="manual"
    )
    provider_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)

    trial_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
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
            "ix_integration_entitlements_org_listing",
            "organization_id", "listing_id", "status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<IntegrationEntitlement {self.organization_id} "
            f"{self.listing_id} {self.status}>"
        )


class UsageRecord(Base, UUIDPrimaryKeyMixin):
    """A metered usage event for an installed integration (Phase 9.4).

    Tenant-scoped and RLS-FORCEd. One row per recorded ``(metric, quantity)``,
    the durable record the metering framework aggregates and prices.
    """

    __tablename__ = "usage_records"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    listing_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("integration_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    entitlement_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("integration_entitlements.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: api_call | action | message | record | compute.
    metric: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_usage_records_org_listing_metric",
            "organization_id", "listing_id", "metric",
        ),
    )

    def __repr__(self) -> str:
        return f"<UsageRecord {self.listing_id} {self.metric}={self.quantity}>"


class RevenueEvent(Base, UUIDPrimaryKeyMixin):
    """A recorded revenue event, split platform/developer (Phase 9.4).

    Tenant-scoped and RLS-FORCEd against the paying workspace. ``developer_org_id``
    attributes the developer share to the integration's publisher (NULL for a
    curated, platform-owned listing) — the foundation for developer revenue
    attribution. ``invoice_reference`` is an opaque marketplace invoice reference;
    no invoice is generated here.
    """

    __tablename__ = "revenue_events"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    listing_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("integration_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("integration_plans.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: The developer the developer share is attributed to. NULL = platform.
    developer_org_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: subscription | usage | one_time.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    gross_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    platform_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    developer_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    #: An opaque reference to the invoice this revenue belongs to, if any.
    invoice_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_revenue_events_org_listing", "organization_id", "listing_id"),
        Index("ix_revenue_events_developer", "developer_org_id"),
    )

    def __repr__(self) -> str:
        return f"<RevenueEvent {self.listing_id} {self.kind} {self.gross_cents}c>"
