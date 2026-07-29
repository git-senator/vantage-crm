"""Marketplace SDK data access. Tenant-scoped reads on top of RLS.

Every table carries ``organization_id``, so ``BaseRepository.get`` already scopes
by tenant; these add the application-oriented lookups the SDK services need, with
the same explicit defence-in-depth predicate every repository applies.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from app.models.marketplace_sdk import (
    MarketplaceApiAccessGrant,
    MarketplaceEventSubscription,
    MarketplaceSdkApplication,
)
from app.repositories.base import BaseRepository


class MarketplaceSdkApplicationRepository(BaseRepository[MarketplaceSdkApplication]):
    model = MarketplaceSdkApplication

    async def get_by_application(
        self, organization_id: UUID, application_id: UUID
    ) -> MarketplaceSdkApplication | None:
        stmt = (
            select(MarketplaceSdkApplication)
            .where(MarketplaceSdkApplication.organization_id == organization_id)
            .where(MarketplaceSdkApplication.application_id == application_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class MarketplaceApiAccessGrantRepository(BaseRepository[MarketplaceApiAccessGrant]):
    model = MarketplaceApiAccessGrant

    async def get_active(
        self, organization_id: UUID, application_id: UUID
    ) -> MarketplaceApiAccessGrant | None:
        stmt = (
            select(MarketplaceApiAccessGrant)
            .where(MarketplaceApiAccessGrant.organization_id == organization_id)
            .where(MarketplaceApiAccessGrant.application_id == application_id)
            .where(MarketplaceApiAccessGrant.revoked_at.is_(None))
            .order_by(MarketplaceApiAccessGrant.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_org(
        self, organization_id: UUID
    ) -> Sequence[MarketplaceApiAccessGrant]:
        stmt = (
            select(MarketplaceApiAccessGrant)
            .where(MarketplaceApiAccessGrant.organization_id == organization_id)
            .order_by(MarketplaceApiAccessGrant.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())


class MarketplaceEventSubscriptionRepository(
    BaseRepository[MarketplaceEventSubscription]
):
    model = MarketplaceEventSubscription

    async def list_for_application(
        self, organization_id: UUID, application_id: UUID
    ) -> Sequence[MarketplaceEventSubscription]:
        stmt = (
            select(MarketplaceEventSubscription)
            .where(MarketplaceEventSubscription.organization_id == organization_id)
            .where(MarketplaceEventSubscription.application_id == application_id)
            .order_by(MarketplaceEventSubscription.event_name.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def find(
        self, organization_id: UUID, application_id: UUID, event_name: str
    ) -> MarketplaceEventSubscription | None:
        stmt = (
            select(MarketplaceEventSubscription)
            .where(MarketplaceEventSubscription.organization_id == organization_id)
            .where(MarketplaceEventSubscription.application_id == application_id)
            .where(MarketplaceEventSubscription.event_name == event_name)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


__all__ = [
    "MarketplaceApiAccessGrantRepository",
    "MarketplaceEventSubscriptionRepository",
    "MarketplaceSdkApplicationRepository",
]
