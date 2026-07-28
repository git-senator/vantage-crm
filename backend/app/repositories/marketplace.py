"""Integration marketplace data access. Tenant-scoped reads on top of RLS.

The listing catalog uses the operational-policy semantics the plugin catalog
does: a tenant sees curated (NULL-publisher) listings plus its own. RLS enforces
that at the database; the ``_visible`` predicate here is the same defence in depth
every repository applies. Discovery filters (category, certification, auth method,
a text query) are pushed into SQL rather than done in Python, so the marketplace
scales past what fits in memory.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select

from app.models.marketplace import IntegrationListing
from app.repositories.base import BaseRepository


class IntegrationListingRepository(BaseRepository[IntegrationListing]):
    model = IntegrationListing

    def _visible(self, organization_id: UUID) -> ColumnElement[bool]:
        return or_(
            IntegrationListing.publisher_organization_id.is_(None),
            IntegrationListing.publisher_organization_id == organization_id,
        )

    async def discover(
        self,
        organization_id: UUID,
        *,
        category: str | None = None,
        certification: str | None = None,
        auth_method: str | None = None,
        query: str | None = None,
    ) -> Sequence[IntegrationListing]:
        stmt = select(IntegrationListing).where(self._visible(organization_id))
        if category:
            stmt = stmt.where(IntegrationListing.category == category)
        if certification:
            stmt = stmt.where(IntegrationListing.certification == certification)
        if auth_method:
            stmt = stmt.where(IntegrationListing.auth_method == auth_method)
        if query:
            like = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(
                    IntegrationListing.name.ilike(like),
                    IntegrationListing.vendor.ilike(like),
                    IntegrationListing.summary.ilike(like),
                )
            )
        stmt = stmt.order_by(IntegrationListing.name.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_visible(
        self, listing_id: UUID, organization_id: UUID
    ) -> IntegrationListing | None:
        stmt = (
            select(IntegrationListing)
            .where(IntegrationListing.id == listing_id)
            .where(self._visible(organization_id))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_key(
        self, organization_id: UUID, key: str
    ) -> IntegrationListing | None:
        stmt = (
            select(IntegrationListing)
            .where(IntegrationListing.key == key)
            .where(self._visible(organization_id))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count_by_category(self, organization_id: UUID) -> dict[str, int]:
        stmt = (
            select(IntegrationListing.category, func.count())
            .where(self._visible(organization_id))
            .group_by(IntegrationListing.category)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: row[1] for row in rows}

    async def count_certified(self, organization_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(IntegrationListing)
            .where(self._visible(organization_id))
            .where(IntegrationListing.certification.in_(("certified", "official")))
        )
        return int((await self.session.execute(stmt)).scalar() or 0)

    async def count_visible(self, organization_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(IntegrationListing)
            .where(self._visible(organization_id))
        )
        return int((await self.session.execute(stmt)).scalar() or 0)


__all__ = ["IntegrationListingRepository"]
