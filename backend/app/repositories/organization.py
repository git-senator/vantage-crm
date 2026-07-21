"""Organization data access."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.models.organization import Organization
from app.models.user import User
from app.repositories.base import BaseRepository


class OrganizationRepository(BaseRepository[Organization]):
    model = Organization

    async def get_current(self, organization_id: UUID) -> Organization | None:
        """Load the caller's own organization.

        RLS restricts `organizations` to the row whose id matches the bound
        tenant, so this cannot return another tenant's record even if the id
        were wrong.
        """
        result = await self.session.execute(
            self._base_query().where(Organization.id == organization_id)
        )
        return result.scalar_one_or_none()

    async def list_members(self, organization_id: UUID) -> list[User]:
        result = await self.session.execute(
            select(User)
            .where(User.organization_id == organization_id)
            .where(User.deleted_at.is_(None))
            .order_by(User.full_name)
        )
        return list(result.scalars().all())

    async def slug_exists(self, slug: str) -> bool:
        result = await self.session.execute(
            select(Organization.id).where(Organization.slug == slug)
        )
        return result.first() is not None
