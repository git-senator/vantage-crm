"""Repository base.

The repository layer is the single chokepoint where tenant scoping and
permission scoping become SQL predicates. Services never build queries and
routers never touch the ORM — that is what keeps authorization from scattering
across the codebase. See docs/ARCHITECTURE.md §4.1.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base


class BaseRepository[ModelT: Base]:
    """Common data access for a single model."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _base_query(self) -> Select[tuple[ModelT]]:
        """Start a query, excluding soft-deleted rows where applicable."""
        query = select(self.model)
        if hasattr(self.model, "deleted_at"):
            query = query.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return query

    def scoped_to_organization(
        self, query: Select[tuple[ModelT]], organization_id: UUID
    ) -> Select[tuple[ModelT]]:
        """Apply the tenant predicate explicitly.

        Row-level security already enforces this at the database. Adding it here
        is deliberate defence in depth: either layer failing alone must not leak
        data across tenants. See docs/DATABASE.md §2.
        """
        if not hasattr(self.model, "organization_id"):
            return query
        return query.where(self.model.organization_id == organization_id)  # type: ignore[attr-defined]

    async def get(self, entity_id: UUID, organization_id: UUID) -> ModelT | None:
        """Fetch by id, always scoped to a tenant.

        `organization_id` is a required argument rather than optional by design:
        an unscoped `get()` is precisely the accident that leaks a record across
        tenants, so the API makes it impossible to write by mistake.
        """
        query = self.scoped_to_organization(
            self._base_query().where(self.model.id == entity_id),  # type: ignore[attr-defined]
            organization_id,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        return entity
