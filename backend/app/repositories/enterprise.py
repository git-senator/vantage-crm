"""Enterprise data access.

Tenant-scoped reads on top of RLS. The singleton tables (`security_policies`,
`organization_branding`, `compliance_policies`, `sso_connections`) expose a
`get_for_org` that returns the one row or None; the service turns None into
deterministic defaults, so a workspace that has never opened the settings screen
still behaves as if it had the shipped policy.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from app.models.enterprise import (
    CompliancePolicy,
    DataRequest,
    FeatureFlag,
    OrganizationBranding,
    SecurityPolicy,
    SsoConnection,
)
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE, Cursor


class SecurityPolicyRepository(BaseRepository[SecurityPolicy]):
    model = SecurityPolicy

    async def get_for_org(self, organization_id: UUID) -> SecurityPolicy | None:
        query = select(SecurityPolicy).where(
            SecurityPolicy.organization_id == organization_id
        )
        return (await self.session.execute(query)).scalar_one_or_none()


class OrganizationBrandingRepository(BaseRepository[OrganizationBranding]):
    model = OrganizationBranding

    async def get_for_org(self, organization_id: UUID) -> OrganizationBranding | None:
        query = select(OrganizationBranding).where(
            OrganizationBranding.organization_id == organization_id
        )
        return (await self.session.execute(query)).scalar_one_or_none()


class CompliancePolicyRepository(BaseRepository[CompliancePolicy]):
    model = CompliancePolicy

    async def get_for_org(self, organization_id: UUID) -> CompliancePolicy | None:
        query = select(CompliancePolicy).where(
            CompliancePolicy.organization_id == organization_id
        )
        return (await self.session.execute(query)).scalar_one_or_none()


class SsoConnectionRepository(BaseRepository[SsoConnection]):
    model = SsoConnection

    async def get_for_org(self, organization_id: UUID) -> SsoConnection | None:
        query = select(SsoConnection).where(
            SsoConnection.organization_id == organization_id
        )
        return (await self.session.execute(query)).scalar_one_or_none()


class FeatureFlagRepository(BaseRepository[FeatureFlag]):
    model = FeatureFlag

    async def list_for_org(self, organization_id: UUID) -> Sequence[FeatureFlag]:
        query = (
            select(FeatureFlag)
            .where(FeatureFlag.organization_id == organization_id)
            .order_by(FeatureFlag.key.asc())
        )
        return list((await self.session.execute(query)).scalars().all())

    async def get_by_key(
        self, organization_id: UUID, key: str
    ) -> FeatureFlag | None:
        query = (
            select(FeatureFlag)
            .where(FeatureFlag.organization_id == organization_id)
            .where(FeatureFlag.key == key)
        )
        return (await self.session.execute(query)).scalar_one_or_none()


class DataRequestRepository(BaseRepository[DataRequest]):
    model = DataRequest

    async def list_for_org(
        self,
        organization_id: UUID,
        *,
        limit: int,
        cursor: Cursor | None = None,
    ) -> tuple[list[DataRequest], bool]:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = select(DataRequest).where(
            DataRequest.organization_id == organization_id
        )
        if cursor is not None:
            query = query.where(
                func.row(DataRequest.created_at, DataRequest.id)
                < func.row(cursor.created_at, cursor.id)
            )
        query = query.order_by(
            DataRequest.created_at.desc(), DataRequest.id.desc()
        ).limit(limit + 1)
        rows = list((await self.session.execute(query)).scalars().all())
        return rows[:limit], len(rows) > limit

    async def list_stale_pending(
        self, organization_id: UUID, *, before: datetime
    ) -> Sequence[DataRequest]:
        """Pending requests whose processing job the fast path may have lost."""
        query = (
            select(DataRequest)
            .where(DataRequest.organization_id == organization_id)
            .where(DataRequest.status == "pending")
            .where(DataRequest.created_at < before)
        )
        return list((await self.session.execute(query)).scalars().all())


__all__ = [
    "CompliancePolicyRepository",
    "DataRequestRepository",
    "FeatureFlagRepository",
    "OrganizationBrandingRepository",
    "SecurityPolicyRepository",
    "SsoConnectionRepository",
]
