"""Developer platform data access. Tenant-scoped reads on top of RLS.

Every table carries ``organization_id``, so ``BaseRepository.get`` already scopes
by tenant; these add the developer- and application-oriented lookups the services
need. The defence-in-depth tenant predicate is applied explicitly, matching every
other repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from app.models.developer import (
    ApplicationVersionReview,
    DeveloperApiCredential,
    DeveloperOrganization,
    MarketplaceApplication,
)
from app.repositories.base import BaseRepository


class DeveloperOrganizationRepository(BaseRepository[DeveloperOrganization]):
    model = DeveloperOrganization

    async def list_for_org(
        self, organization_id: UUID
    ) -> Sequence[DeveloperOrganization]:
        stmt = (
            select(DeveloperOrganization)
            .where(DeveloperOrganization.organization_id == organization_id)
            .order_by(DeveloperOrganization.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())


class MarketplaceApplicationRepository(BaseRepository[MarketplaceApplication]):
    model = MarketplaceApplication

    async def list_for_org(
        self, organization_id: UUID
    ) -> Sequence[MarketplaceApplication]:
        stmt = (
            select(MarketplaceApplication)
            .where(MarketplaceApplication.organization_id == organization_id)
            .order_by(MarketplaceApplication.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_developer(
        self, organization_id: UUID, developer_org_id: UUID
    ) -> Sequence[MarketplaceApplication]:
        stmt = (
            select(MarketplaceApplication)
            .where(MarketplaceApplication.organization_id == organization_id)
            .where(MarketplaceApplication.developer_org_id == developer_org_id)
            .order_by(MarketplaceApplication.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def review_queue(
        self, organization_id: UUID
    ) -> Sequence[MarketplaceApplication]:
        """Applications awaiting a review decision (submitted or in review)."""
        stmt = (
            select(MarketplaceApplication)
            .where(MarketplaceApplication.organization_id == organization_id)
            .where(
                MarketplaceApplication.lifecycle_status.in_(("submitted", "review"))
            )
            .order_by(MarketplaceApplication.submitted_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_slug(
        self, organization_id: UUID, developer_org_id: UUID, slug: str
    ) -> MarketplaceApplication | None:
        stmt = (
            select(MarketplaceApplication)
            .where(MarketplaceApplication.organization_id == organization_id)
            .where(MarketplaceApplication.developer_org_id == developer_org_id)
            .where(MarketplaceApplication.slug == slug)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class ApplicationVersionReviewRepository(BaseRepository[ApplicationVersionReview]):
    model = ApplicationVersionReview

    async def list_for_application(
        self, organization_id: UUID, application_id: UUID
    ) -> Sequence[ApplicationVersionReview]:
        stmt = (
            select(ApplicationVersionReview)
            .where(ApplicationVersionReview.organization_id == organization_id)
            .where(ApplicationVersionReview.application_id == application_id)
            .order_by(ApplicationVersionReview.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())


class DeveloperApiCredentialRepository(BaseRepository[DeveloperApiCredential]):
    model = DeveloperApiCredential

    async def list_for_developer(
        self, organization_id: UUID, developer_org_id: UUID
    ) -> Sequence[DeveloperApiCredential]:
        stmt = (
            select(DeveloperApiCredential)
            .where(DeveloperApiCredential.organization_id == organization_id)
            .where(DeveloperApiCredential.developer_org_id == developer_org_id)
            .order_by(DeveloperApiCredential.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())


__all__ = [
    "ApplicationVersionReviewRepository",
    "DeveloperApiCredentialRepository",
    "DeveloperOrganizationRepository",
    "MarketplaceApplicationRepository",
]
