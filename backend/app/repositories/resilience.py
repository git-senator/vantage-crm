"""Operational resilience data access. Tenant-scoped reads on top of RLS."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select

from app.models.resilience import (
    BusinessService,
    ContinuityPlan,
    OperationalIncident,
    PostIncidentReview,
    ServiceDependency,
)
from app.repositories.base import BaseRepository


class BusinessServiceRepository(BaseRepository[BusinessService]):
    model = BusinessService

    async def list_for_org(
        self,
        organization_id: UUID,
        *,
        criticality: str | None = None,
        active_only: bool = False,
    ) -> Sequence[BusinessService]:
        query = select(BusinessService).where(
            BusinessService.organization_id == organization_id
        )
        if criticality:
            query = query.where(BusinessService.criticality == criticality)
        if active_only:
            query = query.where(BusinessService.is_active.is_(True))
        query = query.order_by(BusinessService.name.asc())
        return list((await self.session.execute(query)).scalars().all())

    async def get_by_name(
        self, organization_id: UUID, name: str
    ) -> BusinessService | None:
        query = (
            select(BusinessService)
            .where(BusinessService.organization_id == organization_id)
            .where(BusinessService.name == name)
        )
        return (await self.session.execute(query)).scalar_one_or_none()


class ServiceDependencyRepository(BaseRepository[ServiceDependency]):
    model = ServiceDependency

    async def list_for_org(
        self, organization_id: UUID
    ) -> Sequence[ServiceDependency]:
        query = (
            select(ServiceDependency)
            .where(ServiceDependency.organization_id == organization_id)
            .order_by(ServiceDependency.created_at.desc())
        )
        return list((await self.session.execute(query)).scalars().all())

    async def list_for_service(
        self, organization_id: UUID, service_id: UUID
    ) -> Sequence[ServiceDependency]:
        """Every edge touching the service, as dependent or dependency."""
        query = (
            select(ServiceDependency)
            .where(ServiceDependency.organization_id == organization_id)
            .where(
                or_(
                    ServiceDependency.service_id == service_id,
                    ServiceDependency.depends_on_id == service_id,
                )
            )
        )
        return list((await self.session.execute(query)).scalars().all())

    async def find_pair(
        self, organization_id: UUID, service_id: UUID, depends_on_id: UUID
    ) -> ServiceDependency | None:
        query = (
            select(ServiceDependency)
            .where(ServiceDependency.organization_id == organization_id)
            .where(ServiceDependency.service_id == service_id)
            .where(ServiceDependency.depends_on_id == depends_on_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def count_for_org(self, organization_id: UUID) -> int:
        query = (
            select(func.count())
            .select_from(ServiceDependency)
            .where(ServiceDependency.organization_id == organization_id)
        )
        return int((await self.session.execute(query)).scalar() or 0)


class ContinuityPlanRepository(BaseRepository[ContinuityPlan]):
    model = ContinuityPlan

    async def list_for_org(
        self,
        organization_id: UUID,
        *,
        plan_type: str | None = None,
        status: str | None = None,
    ) -> Sequence[ContinuityPlan]:
        query = select(ContinuityPlan).where(
            ContinuityPlan.organization_id == organization_id
        )
        if plan_type:
            query = query.where(ContinuityPlan.plan_type == plan_type)
        if status:
            query = query.where(ContinuityPlan.status == status)
        query = query.order_by(ContinuityPlan.created_at.desc())
        return list((await self.session.execute(query)).scalars().all())


class OperationalIncidentRepository(BaseRepository[OperationalIncident]):
    model = OperationalIncident

    async def list_for_org(
        self,
        organization_id: UUID,
        *,
        status: str | None = None,
        severity: str | None = None,
        since: datetime | None = None,
    ) -> Sequence[OperationalIncident]:
        query = select(OperationalIncident).where(
            OperationalIncident.organization_id == organization_id
        )
        if status:
            query = query.where(OperationalIncident.status == status)
        if severity:
            query = query.where(OperationalIncident.severity == severity)
        if since is not None:
            query = query.where(OperationalIncident.started_at >= since)
        query = query.order_by(OperationalIncident.started_at.desc())
        return list((await self.session.execute(query)).scalars().all())


class PostIncidentReviewRepository(BaseRepository[PostIncidentReview]):
    model = PostIncidentReview

    async def get_for_incident(
        self, organization_id: UUID, incident_id: UUID
    ) -> PostIncidentReview | None:
        query = (
            select(PostIncidentReview)
            .where(PostIncidentReview.organization_id == organization_id)
            .where(PostIncidentReview.incident_id == incident_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()


__all__ = [
    "BusinessServiceRepository",
    "ContinuityPlanRepository",
    "OperationalIncidentRepository",
    "PostIncidentReviewRepository",
    "ServiceDependencyRepository",
]
