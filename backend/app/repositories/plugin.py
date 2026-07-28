"""Plugin platform data access. Tenant-scoped reads on top of RLS.

The catalog read is deliberately explicit about the operational-policy semantics:
a tenant sees first-party (NULL-publisher) plugins plus its own. RLS enforces this
at the database; the predicate here is the same defence-in-depth every repository
applies.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, or_, select

from app.models.plugin import Plugin, PluginEventSubscription, PluginInstallation
from app.repositories.base import BaseRepository


class PluginRepository(BaseRepository[Plugin]):
    model = Plugin

    def _visible(self, organization_id: UUID):  # type: ignore[no-untyped-def]
        return or_(
            Plugin.publisher_organization_id.is_(None),
            Plugin.publisher_organization_id == organization_id,
        )

    async def list_catalog(
        self, organization_id: UUID, *, category: str | None = None
    ) -> Sequence[Plugin]:
        query = select(Plugin).where(self._visible(organization_id))
        if category:
            query = query.where(Plugin.category == category)
        query = query.order_by(Plugin.name.asc())
        return list((await self.session.execute(query)).scalars().all())

    async def get_visible(
        self, plugin_id: UUID, organization_id: UUID
    ) -> Plugin | None:
        query = (
            select(Plugin)
            .where(Plugin.id == plugin_id)
            .where(self._visible(organization_id))
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_by_key(
        self, organization_id: UUID, key: str
    ) -> Plugin | None:
        query = (
            select(Plugin)
            .where(Plugin.key == key)
            .where(self._visible(organization_id))
        )
        return (await self.session.execute(query)).scalar_one_or_none()


class PluginInstallationRepository(BaseRepository[PluginInstallation]):
    model = PluginInstallation

    async def list_for_org(
        self, organization_id: UUID, *, status: str | None = None
    ) -> Sequence[PluginInstallation]:
        query = select(PluginInstallation).where(
            PluginInstallation.organization_id == organization_id
        )
        if status:
            query = query.where(PluginInstallation.status == status)
        query = query.order_by(PluginInstallation.created_at.desc())
        return list((await self.session.execute(query)).scalars().all())

    async def get_by_plugin(
        self, organization_id: UUID, plugin_id: UUID
    ) -> PluginInstallation | None:
        query = (
            select(PluginInstallation)
            .where(PluginInstallation.organization_id == organization_id)
            .where(PluginInstallation.plugin_id == plugin_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def count_for_org(self, organization_id: UUID) -> int:
        query = (
            select(func.count())
            .select_from(PluginInstallation)
            .where(PluginInstallation.organization_id == organization_id)
        )
        return int((await self.session.execute(query)).scalar() or 0)

    async def count_by_status(self, organization_id: UUID) -> dict[str, int]:
        query = (
            select(PluginInstallation.status, func.count())
            .where(PluginInstallation.organization_id == organization_id)
            .group_by(PluginInstallation.status)
        )
        rows = (await self.session.execute(query)).all()
        return {row[0]: row[1] for row in rows}


class PluginEventSubscriptionRepository(BaseRepository[PluginEventSubscription]):
    model = PluginEventSubscription

    async def list_for_installation(
        self, organization_id: UUID, installation_id: UUID
    ) -> Sequence[PluginEventSubscription]:
        query = (
            select(PluginEventSubscription)
            .where(PluginEventSubscription.organization_id == organization_id)
            .where(PluginEventSubscription.installation_id == installation_id)
            .order_by(PluginEventSubscription.event_type.asc())
        )
        return list((await self.session.execute(query)).scalars().all())

    async def find(
        self, organization_id: UUID, installation_id: UUID, event_type: str
    ) -> PluginEventSubscription | None:
        query = (
            select(PluginEventSubscription)
            .where(PluginEventSubscription.organization_id == organization_id)
            .where(PluginEventSubscription.installation_id == installation_id)
            .where(PluginEventSubscription.event_type == event_type)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def subscribers(
        self, organization_id: UUID, event_type: str
    ) -> Sequence[PluginInstallation]:
        """Enabled installations subscribed to an event — the dispatch targets."""
        query = (
            select(PluginInstallation)
            .join(
                PluginEventSubscription,
                PluginEventSubscription.installation_id == PluginInstallation.id,
            )
            .where(PluginInstallation.organization_id == organization_id)
            .where(PluginInstallation.status == "enabled")
            .where(PluginEventSubscription.event_type == event_type)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def count_for_org(self, organization_id: UUID) -> int:
        query = (
            select(func.count())
            .select_from(PluginEventSubscription)
            .where(PluginEventSubscription.organization_id == organization_id)
        )
        return int((await self.session.execute(query)).scalar() or 0)


__all__ = [
    "PluginEventSubscriptionRepository",
    "PluginInstallationRepository",
    "PluginRepository",
]
