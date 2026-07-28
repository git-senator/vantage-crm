"""App marketplace & plugin platform models (Phase 9.0).

Three tables:

  * ``Plugin`` — the marketplace catalog entry. Its RLS uses the *operational*
    policy: a row with a NULL ``publisher_organization_id`` is a first-party
    plugin visible to every tenant (the shared marketplace), while a row with a
    publisher is that tenant's private plugin, visible only to them. This is the
    same pattern ``job_failures`` uses for tenant-less infrastructure rows.
  * ``PluginInstallation`` — a tenant's install of a plugin: its lifecycle state,
    the capabilities it was granted, and its configuration (non-secret in
    ``config``; secret values sealed at rest in ``secrets``). Tenant-scoped.
  * ``PluginEventSubscription`` — which CRM events an installation receives.
    Tenant-scoped, and its event vocabulary is the webhook event set.

The platform reuses RBAC (capabilities map to permissions), feature flags (a
manifest may require one to install), the webhook event vocabulary, and audit
logging. No integration, webhook, or automation machinery is restated here.
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


def _actor_fk() -> Mapped[UUID | None]:
    return mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class Plugin(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "plugins"

    #: NULL = first-party, globally visible. Non-NULL = a tenant's private plugin.
    #: The operational RLS policy keys on this column.
    publisher_organization_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_by: Mapped[UUID | None] = _actor_fk()

    #: The globally-unique marketplace identifier (npm-style namespace).
    key: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    publisher_name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)

    #: The full validated manifest, kept verbatim for the marketplace listing.
    manifest: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )
    capabilities: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    event_types: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    config_schema: Mapped[list[dict[str, Any]]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    required_feature: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_key: Mapped[str | None] = mapped_column(String(60), nullable=True)

    is_first_party: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
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
        UniqueConstraint("key", name="uq_plugins_key"),
        Index("ix_plugins_publisher", "publisher_organization_id"),
        Index("ix_plugins_category", "category"),
    )

    def __repr__(self) -> str:
        return f"<Plugin {self.key!r} v{self.version}>"


class PluginInstallation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "plugin_installations"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    installed_by: Mapped[UUID | None] = _actor_fk()
    #: RESTRICT: a plugin with live installations cannot be deleted out from
    #: under them.
    plugin_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("plugins.id", ondelete="RESTRICT"),
        nullable=False,
    )

    #: installed | enabled | disabled. Uninstalling deletes the row.
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="installed"
    )
    granted_capabilities: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    #: Non-secret configuration. Secret values live in `secrets`, sealed.
    config: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )
    #: Secret configuration values, each a SecretBox token. Never returned in the
    #: clear; a read reports only which keys are set.
    secrets: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )

    enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
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
            "organization_id", "plugin_id", name="uq_plugin_installations_org_plugin"
        ),
        Index("ix_plugin_installations_org", "organization_id", "status"),
    )


class PluginEventSubscription(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "plugin_event_subscriptions"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    installation_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("plugin_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "installation_id", "event_type",
            name="uq_plugin_event_subscriptions",
        ),
        Index(
            "ix_plugin_event_subscriptions_org_event",
            "organization_id", "event_type",
        ),
    )
