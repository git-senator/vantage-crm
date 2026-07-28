"""Compliance operations data access. Tenant-scoped reads on top of RLS."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select

from app.models.compliance_ops import ComplianceEvidence, DataProcessingActivity
from app.repositories.base import BaseRepository


class DataProcessingActivityRepository(BaseRepository[DataProcessingActivity]):
    model = DataProcessingActivity

    async def list_for_org(
        self, organization_id: UUID
    ) -> Sequence[DataProcessingActivity]:
        query = (
            select(DataProcessingActivity)
            .where(DataProcessingActivity.organization_id == organization_id)
            .order_by(DataProcessingActivity.created_at.desc())
        )
        return list((await self.session.execute(query)).scalars().all())

    async def count_for_org(self, organization_id: UUID) -> int:
        query = (
            select(func.count())
            .select_from(DataProcessingActivity)
            .where(DataProcessingActivity.organization_id == organization_id)
            .where(DataProcessingActivity.is_active.is_(True))
        )
        return int((await self.session.execute(query)).scalar() or 0)


class ComplianceEvidenceRepository(BaseRepository[ComplianceEvidence]):
    model = ComplianceEvidence

    async def list_for_org(
        self, organization_id: UUID, *, control_key: str | None = None
    ) -> Sequence[ComplianceEvidence]:
        query = select(ComplianceEvidence).where(
            ComplianceEvidence.organization_id == organization_id
        )
        if control_key:
            query = query.where(ComplianceEvidence.control_key == control_key)
        query = query.order_by(ComplianceEvidence.collected_at.desc())
        return list((await self.session.execute(query)).scalars().all())

    async def controls_with_evidence(self, organization_id: UUID) -> set[str]:
        """The distinct control keys that have at least one evidence record —
        the input the checks framework uses to let evidence satisfy a control."""
        query = (
            select(ComplianceEvidence.control_key)
            .where(ComplianceEvidence.organization_id == organization_id)
            .distinct()
        )
        rows = (await self.session.execute(query)).scalars().all()
        return set(rows)


__all__ = [
    "ComplianceEvidenceRepository",
    "DataProcessingActivityRepository",
]
