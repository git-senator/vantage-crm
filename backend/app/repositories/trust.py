"""Trust & risk management data access. Tenant-scoped reads on top of RLS."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from app.models.trust import (
    Certification,
    QuestionnaireItem,
    Risk,
    TrustProfile,
)
from app.repositories.base import BaseRepository


class RiskRepository(BaseRepository[Risk]):
    model = Risk

    async def list_for_org(
        self,
        organization_id: UUID,
        *,
        status: str | None = None,
        category: str | None = None,
    ) -> Sequence[Risk]:
        query = select(Risk).where(Risk.organization_id == organization_id)
        if status:
            query = query.where(Risk.status == status)
        if category:
            query = query.where(Risk.category == category)
        query = query.order_by(
            Risk.residual_score.desc(), Risk.created_at.desc()
        )
        return list((await self.session.execute(query)).scalars().all())


class CertificationRepository(BaseRepository[Certification]):
    model = Certification

    async def list_for_org(
        self, organization_id: UUID, *, status: str | None = None
    ) -> Sequence[Certification]:
        query = select(Certification).where(
            Certification.organization_id == organization_id
        )
        if status:
            query = query.where(Certification.status == status)
        query = query.order_by(Certification.framework.asc())
        return list((await self.session.execute(query)).scalars().all())

    async def get_for_framework(
        self, organization_id: UUID, framework: str
    ) -> Certification | None:
        query = (
            select(Certification)
            .where(Certification.organization_id == organization_id)
            .where(Certification.framework == framework)
        )
        return (await self.session.execute(query)).scalar_one_or_none()


class TrustProfileRepository(BaseRepository[TrustProfile]):
    model = TrustProfile

    async def get_for_org(self, organization_id: UUID) -> TrustProfile | None:
        query = select(TrustProfile).where(
            TrustProfile.organization_id == organization_id
        )
        return (await self.session.execute(query)).scalar_one_or_none()


class QuestionnaireItemRepository(BaseRepository[QuestionnaireItem]):
    model = QuestionnaireItem

    async def list_for_org(
        self, organization_id: UUID, *, public_only: bool = False
    ) -> Sequence[QuestionnaireItem]:
        query = select(QuestionnaireItem).where(
            QuestionnaireItem.organization_id == organization_id
        )
        if public_only:
            query = query.where(QuestionnaireItem.is_public.is_(True))
        query = query.order_by(
            QuestionnaireItem.sort_order.asc(), QuestionnaireItem.created_at.asc()
        )
        return list((await self.session.execute(query)).scalars().all())


__all__ = [
    "CertificationRepository",
    "QuestionnaireItemRepository",
    "RiskRepository",
    "TrustProfileRepository",
]
