"""Data governance data access. Tenant-scoped reads on top of RLS."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select

from app.models.governance import DataAsset, DataLineageEdge, DataQualityRule
from app.repositories.base import BaseRepository


class DataAssetRepository(BaseRepository[DataAsset]):
    model = DataAsset

    async def list_for_org(
        self,
        organization_id: UUID,
        *,
        classification: str | None = None,
        asset_type: str | None = None,
        contains_pii: bool | None = None,
        owner_id: UUID | None = None,
    ) -> Sequence[DataAsset]:
        query = select(DataAsset).where(DataAsset.organization_id == organization_id)
        if classification:
            query = query.where(DataAsset.classification == classification)
        if asset_type:
            query = query.where(DataAsset.asset_type == asset_type)
        if contains_pii is not None:
            query = query.where(DataAsset.contains_pii.is_(contains_pii))
        if owner_id is not None:
            query = query.where(DataAsset.owner_id == owner_id)
        query = query.order_by(DataAsset.name.asc())
        return list((await self.session.execute(query)).scalars().all())

    async def get_by_name(
        self, organization_id: UUID, name: str
    ) -> DataAsset | None:
        query = (
            select(DataAsset)
            .where(DataAsset.organization_id == organization_id)
            .where(DataAsset.name == name)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def count_by_classification(
        self, organization_id: UUID
    ) -> dict[str, int]:
        query = (
            select(DataAsset.classification, func.count())
            .where(DataAsset.organization_id == organization_id)
            .group_by(DataAsset.classification)
        )
        rows = (await self.session.execute(query)).all()
        return {row[0]: row[1] for row in rows}

    async def count_for_org(self, organization_id: UUID) -> int:
        query = (
            select(func.count())
            .select_from(DataAsset)
            .where(DataAsset.organization_id == organization_id)
        )
        return int((await self.session.execute(query)).scalar() or 0)

    async def count_owned(self, organization_id: UUID) -> int:
        query = (
            select(func.count())
            .select_from(DataAsset)
            .where(DataAsset.organization_id == organization_id)
            .where(DataAsset.owner_id.isnot(None))
        )
        return int((await self.session.execute(query)).scalar() or 0)

    async def count_sensitive(self, organization_id: UUID) -> int:
        """Assets at or above `confidential`, or carrying any privacy label."""
        query = (
            select(func.count())
            .select_from(DataAsset)
            .where(DataAsset.organization_id == organization_id)
            .where(
                or_(
                    DataAsset.classification.in_(("confidential", "restricted")),
                    DataAsset.contains_pii.is_(True),
                )
            )
        )
        return int((await self.session.execute(query)).scalar() or 0)

    async def count_unreviewed(
        self, organization_id: UUID, *, before: datetime
    ) -> int:
        """Active assets never reviewed, or last reviewed before the cutoff."""
        query = (
            select(func.count())
            .select_from(DataAsset)
            .where(DataAsset.organization_id == organization_id)
            .where(DataAsset.is_active.is_(True))
            .where(
                or_(
                    DataAsset.last_reviewed_at.is_(None),
                    DataAsset.last_reviewed_at < before,
                )
            )
        )
        return int((await self.session.execute(query)).scalar() or 0)


class DataQualityRuleRepository(BaseRepository[DataQualityRule]):
    model = DataQualityRule

    async def list_for_org(
        self, organization_id: UUID, *, active_only: bool = False
    ) -> Sequence[DataQualityRule]:
        query = select(DataQualityRule).where(
            DataQualityRule.organization_id == organization_id
        )
        if active_only:
            query = query.where(DataQualityRule.is_active.is_(True))
        query = query.order_by(DataQualityRule.created_at.desc())
        return list((await self.session.execute(query)).scalars().all())

    async def list_for_asset(
        self, organization_id: UUID, asset_id: UUID
    ) -> Sequence[DataQualityRule]:
        query = (
            select(DataQualityRule)
            .where(DataQualityRule.organization_id == organization_id)
            .where(DataQualityRule.asset_id == asset_id)
            .order_by(DataQualityRule.created_at.asc())
        )
        return list((await self.session.execute(query)).scalars().all())


class DataLineageRepository(BaseRepository[DataLineageEdge]):
    model = DataLineageEdge

    async def list_for_org(
        self, organization_id: UUID
    ) -> Sequence[DataLineageEdge]:
        query = (
            select(DataLineageEdge)
            .where(DataLineageEdge.organization_id == organization_id)
            .order_by(DataLineageEdge.created_at.desc())
        )
        return list((await self.session.execute(query)).scalars().all())

    async def list_for_asset(
        self, organization_id: UUID, asset_id: UUID
    ) -> Sequence[DataLineageEdge]:
        """Every edge touching the asset, upstream or downstream."""
        query = (
            select(DataLineageEdge)
            .where(DataLineageEdge.organization_id == organization_id)
            .where(
                or_(
                    DataLineageEdge.upstream_asset_id == asset_id,
                    DataLineageEdge.downstream_asset_id == asset_id,
                )
            )
        )
        return list((await self.session.execute(query)).scalars().all())

    async def find_pair(
        self, organization_id: UUID, upstream_id: UUID, downstream_id: UUID
    ) -> DataLineageEdge | None:
        query = (
            select(DataLineageEdge)
            .where(DataLineageEdge.organization_id == organization_id)
            .where(DataLineageEdge.upstream_asset_id == upstream_id)
            .where(DataLineageEdge.downstream_asset_id == downstream_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def count_for_org(self, organization_id: UUID) -> int:
        query = (
            select(func.count())
            .select_from(DataLineageEdge)
            .where(DataLineageEdge.organization_id == organization_id)
        )
        return int((await self.session.execute(query)).scalar() or 0)


__all__ = [
    "DataAssetRepository",
    "DataLineageRepository",
    "DataQualityRuleRepository",
]
