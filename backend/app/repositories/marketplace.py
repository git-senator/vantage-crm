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

from app.models.marketplace import (
    IntegrationEntitlement,
    IntegrationInstallation,
    IntegrationListing,
    IntegrationPlan,
    IntegrationReview,
    IntegrationVersion,
    RevenueEvent,
    UsageRecord,
)
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

    async def count_by_status(self, organization_id: UUID) -> dict[str, int]:
        stmt = (
            select(IntegrationListing.status, func.count())
            .where(self._visible(organization_id))
            .group_by(IntegrationListing.status)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: row[1] for row in rows}


class IntegrationVersionRepository(BaseRepository[IntegrationVersion]):
    model = IntegrationVersion

    def _visible(self, organization_id: UUID) -> ColumnElement[bool]:
        return or_(
            IntegrationVersion.publisher_organization_id.is_(None),
            IntegrationVersion.publisher_organization_id == organization_id,
        )

    async def list_for_listing(
        self, listing_id: UUID, organization_id: UUID
    ) -> Sequence[IntegrationVersion]:
        stmt = (
            select(IntegrationVersion)
            .where(IntegrationVersion.listing_id == listing_id)
            .where(self._visible(organization_id))
            .order_by(IntegrationVersion.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_version(
        self, listing_id: UUID, version: str, organization_id: UUID
    ) -> IntegrationVersion | None:
        stmt = (
            select(IntegrationVersion)
            .where(IntegrationVersion.listing_id == listing_id)
            .where(IntegrationVersion.version == version)
            .where(self._visible(organization_id))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def latest_published(
        self, listing_id: UUID, organization_id: UUID
    ) -> IntegrationVersion | None:
        """The most recently created published version of a listing."""
        stmt = (
            select(IntegrationVersion)
            .where(IntegrationVersion.listing_id == listing_id)
            .where(IntegrationVersion.status == "published")
            .where(self._visible(organization_id))
            .order_by(IntegrationVersion.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class IntegrationInstallationRepository(BaseRepository[IntegrationInstallation]):
    model = IntegrationInstallation

    async def list_active_for_org(
        self, organization_id: UUID
    ) -> Sequence[IntegrationInstallation]:
        stmt = (
            select(IntegrationInstallation)
            .where(IntegrationInstallation.organization_id == organization_id)
            .where(IntegrationInstallation.status.in_(("active", "upgrading")))
            .order_by(IntegrationInstallation.installed_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def history_for_org(
        self, organization_id: UUID
    ) -> Sequence[IntegrationInstallation]:
        stmt = (
            select(IntegrationInstallation)
            .where(IntegrationInstallation.organization_id == organization_id)
            .order_by(IntegrationInstallation.installed_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_active(
        self, organization_id: UUID, listing_id: UUID
    ) -> IntegrationInstallation | None:
        stmt = (
            select(IntegrationInstallation)
            .where(IntegrationInstallation.organization_id == organization_id)
            .where(IntegrationInstallation.listing_id == listing_id)
            .where(IntegrationInstallation.status.in_(("active", "upgrading")))
            .order_by(IntegrationInstallation.installed_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count_active_by_listing(
        self, organization_id: UUID
    ) -> dict[UUID, int]:
        """Active-install counts per listing for adoption metrics."""
        stmt = (
            select(IntegrationInstallation.listing_id, func.count())
            .where(IntegrationInstallation.organization_id == organization_id)
            .where(IntegrationInstallation.status.in_(("active", "upgrading")))
            .group_by(IntegrationInstallation.listing_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: row[1] for row in rows}

    async def count_active(self, organization_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(IntegrationInstallation)
            .where(IntegrationInstallation.organization_id == organization_id)
            .where(IntegrationInstallation.status.in_(("active", "upgrading")))
        )
        return int((await self.session.execute(stmt)).scalar() or 0)


class IntegrationReviewRepository(BaseRepository[IntegrationReview]):
    model = IntegrationReview

    async def list_for_listing(
        self, listing_id: UUID, organization_id: UUID
    ) -> Sequence[IntegrationReview]:
        stmt = (
            select(IntegrationReview)
            .where(IntegrationReview.organization_id == organization_id)
            .where(IntegrationReview.listing_id == listing_id)
            .order_by(IntegrationReview.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())


class IntegrationPlanRepository(BaseRepository[IntegrationPlan]):
    model = IntegrationPlan

    def _visible(self, organization_id: UUID) -> ColumnElement[bool]:
        return or_(
            IntegrationPlan.publisher_organization_id.is_(None),
            IntegrationPlan.publisher_organization_id == organization_id,
        )

    async def list_for_listing(
        self, listing_id: UUID, organization_id: UUID, *, active_only: bool = False
    ) -> Sequence[IntegrationPlan]:
        stmt = (
            select(IntegrationPlan)
            .where(IntegrationPlan.listing_id == listing_id)
            .where(self._visible(organization_id))
        )
        if active_only:
            stmt = stmt.where(IntegrationPlan.is_active.is_(True))
        stmt = stmt.order_by(IntegrationPlan.amount_cents.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_visible(
        self, plan_id: UUID, organization_id: UUID
    ) -> IntegrationPlan | None:
        stmt = (
            select(IntegrationPlan)
            .where(IntegrationPlan.id == plan_id)
            .where(self._visible(organization_id))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_key(
        self, listing_id: UUID, key: str, organization_id: UUID
    ) -> IntegrationPlan | None:
        stmt = (
            select(IntegrationPlan)
            .where(IntegrationPlan.listing_id == listing_id)
            .where(IntegrationPlan.key == key)
            .where(self._visible(organization_id))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count_active(self, organization_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(IntegrationPlan)
            .where(self._visible(organization_id))
            .where(IntegrationPlan.is_active.is_(True))
        )
        return int((await self.session.execute(stmt)).scalar() or 0)


class IntegrationEntitlementRepository(BaseRepository[IntegrationEntitlement]):
    model = IntegrationEntitlement

    async def get_active(
        self, organization_id: UUID, listing_id: UUID
    ) -> IntegrationEntitlement | None:
        stmt = (
            select(IntegrationEntitlement)
            .where(IntegrationEntitlement.organization_id == organization_id)
            .where(IntegrationEntitlement.listing_id == listing_id)
            .where(IntegrationEntitlement.status.in_(("trialing", "active")))
            .order_by(IntegrationEntitlement.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_org(
        self, organization_id: UUID
    ) -> Sequence[IntegrationEntitlement]:
        stmt = (
            select(IntegrationEntitlement)
            .where(IntegrationEntitlement.organization_id == organization_id)
            .order_by(IntegrationEntitlement.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_active(self, organization_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(IntegrationEntitlement)
            .where(IntegrationEntitlement.organization_id == organization_id)
            .where(IntegrationEntitlement.status.in_(("trialing", "active")))
        )
        return int((await self.session.execute(stmt)).scalar() or 0)


class UsageRecordRepository(BaseRepository[UsageRecord]):
    model = UsageRecord

    async def aggregate_by_metric(
        self, organization_id: UUID, listing_id: UUID
    ) -> dict[str, int]:
        stmt = (
            select(UsageRecord.metric, func.coalesce(func.sum(UsageRecord.quantity), 0))
            .where(UsageRecord.organization_id == organization_id)
            .where(UsageRecord.listing_id == listing_id)
            .group_by(UsageRecord.metric)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: int(row[1]) for row in rows}

    async def total_quantity(
        self, organization_id: UUID, listing_id: UUID
    ) -> int:
        stmt = (
            select(func.coalesce(func.sum(UsageRecord.quantity), 0))
            .where(UsageRecord.organization_id == organization_id)
            .where(UsageRecord.listing_id == listing_id)
        )
        return int((await self.session.execute(stmt)).scalar() or 0)


class RevenueEventRepository(BaseRepository[RevenueEvent]):
    model = RevenueEvent

    async def list_for_org(
        self, organization_id: UUID
    ) -> Sequence[RevenueEvent]:
        stmt = (
            select(RevenueEvent)
            .where(RevenueEvent.organization_id == organization_id)
            .order_by(RevenueEvent.occurred_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def totals(self, organization_id: UUID) -> dict[str, int]:
        stmt = (
            select(
                func.coalesce(func.sum(RevenueEvent.gross_cents), 0),
                func.coalesce(func.sum(RevenueEvent.platform_cents), 0),
                func.coalesce(func.sum(RevenueEvent.developer_cents), 0),
            )
            .where(RevenueEvent.organization_id == organization_id)
        )
        row = (await self.session.execute(stmt)).one()
        return {
            "gross_cents": int(row[0]),
            "platform_cents": int(row[1]),
            "developer_cents": int(row[2]),
        }

    async def gross_by_kind(self, organization_id: UUID) -> dict[str, int]:
        stmt = (
            select(RevenueEvent.kind, func.coalesce(func.sum(RevenueEvent.gross_cents), 0))
            .where(RevenueEvent.organization_id == organization_id)
            .group_by(RevenueEvent.kind)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: int(row[1]) for row in rows}

    async def developer_share(
        self, organization_id: UUID, developer_org_id: UUID
    ) -> int:
        """The developer share attributed to a given developer org — the
        attribution foundation."""
        stmt = (
            select(func.coalesce(func.sum(RevenueEvent.developer_cents), 0))
            .where(RevenueEvent.organization_id == organization_id)
            .where(RevenueEvent.developer_org_id == developer_org_id)
        )
        return int((await self.session.execute(stmt)).scalar() or 0)


__all__ = [
    "IntegrationEntitlementRepository",
    "IntegrationInstallationRepository",
    "IntegrationListingRepository",
    "IntegrationPlanRepository",
    "IntegrationReviewRepository",
    "IntegrationVersionRepository",
    "RevenueEventRepository",
    "UsageRecordRepository",
]
