"""Property data access.

Follows the LeadRepository pattern — see `app/repositories/lead.py` for why
scope is a WHERE clause, why tenant scoping is applied here as well as by RLS,
and why pagination is keyset rather than OFFSET.

The one structural difference: `_visible` takes the scope anchor as
`agent_ids` rather than `owner_ids`, because on a property the scope anchor is
the listing agent. For a plain agent that list is `None` (ALL scope), so the
predicate disappears entirely and every agent sees the whole inventory.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import joinedload

from app.models.property import Property
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE, Cursor
from app.schemas.property import PropertyFilters


class PropertyRepository(BaseRepository[Property]):
    model = Property

    # ------------------------------------------------------------- queries

    def _visible(
        self, organization_id: UUID, agent_ids: list[UUID] | None
    ) -> Select[tuple[Property]]:
        """Base query: tenant-scoped, RBAC-scoped, soft-deletes excluded.

        `agent_ids is None` means the caller holds ALL scope — no predicate at
        all, rather than an IN clause listing every user. For properties that
        is the common case rather than the admin case, because listings are
        shared inventory.
        """
        query = self.scoped_to_organization(self._base_query(), organization_id)
        if agent_ids is not None:
            query = query.where(Property.listing_agent_id.in_(agent_ids))
        return query.options(joinedload(Property.listing_agent))

    @staticmethod
    def _apply_filters(
        query: Select[tuple[Property]], filters: PropertyFilters
    ) -> Select[tuple[Property]]:
        if filters.status:
            query = query.where(Property.status == filters.status)
        if filters.property_type:
            query = query.where(Property.property_type == filters.property_type)
        if filters.listing_kind:
            query = query.where(Property.listing_kind == filters.listing_kind)
        if filters.listing_agent_id:
            # Narrows within the caller's scope; it cannot widen it, because
            # the scope predicate is already applied.
            query = query.where(
                Property.listing_agent_id == filters.listing_agent_id
            )
        if filters.client_id:
            query = query.where(Property.client_id == filters.client_id)
        if filters.city:
            # Case-insensitive: users type "san francisco".
            query = query.where(func.lower(Property.city) == filters.city.lower())
        if filters.min_price is not None:
            query = query.where(Property.price >= filters.min_price)
        if filters.max_price is not None:
            query = query.where(Property.price <= filters.max_price)
        if filters.min_bedrooms is not None:
            query = query.where(Property.bedrooms >= filters.min_bedrooms)
        if filters.feature:
            query = query.where(Property.features.contains([filters.feature]))

        if filters.search:
            term = filters.search.strip()
            if term:
                # Full-text over the generated tsvector, OR trigram similarity
                # on the address. `websearch_to_tsquery` accepts human input
                # safely — to_tsquery raises on characters users type
                # constantly.
                #
                # The trigram expression must match ix_properties_address_trgm
                # exactly, or the index is not used.
                address_expression = func.concat(
                    Property.address_line1, " ", Property.city
                )
                query = query.where(
                    or_(
                        Property.search_vector.op("@@")(
                            func.websearch_to_tsquery("simple", term)
                        ),
                        address_expression.op("%")(term),
                    )
                )
        return query

    async def list_page(
        self,
        organization_id: UUID,
        *,
        agent_ids: list[UUID] | None,
        filters: PropertyFilters,
        limit: int,
        cursor: Cursor | None = None,
        ascending: bool = False,
    ) -> tuple[list[Property], bool]:
        """One page, ordered by creation, newest first unless `ascending`.

        Fetches limit+1 to detect a further page without a second COUNT query.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))

        query = self._apply_filters(self._visible(organization_id, agent_ids), filters)

        if cursor is not None:
            # Row-value comparison, which PostgreSQL can satisfy directly from
            # the (created_at, id) index.
            position = func.row(Property.created_at, Property.id)
            anchor = func.row(cursor.created_at, cursor.id)
            query = query.where(position > anchor if ascending else position < anchor)

        if ascending:
            query = query.order_by(Property.created_at.asc(), Property.id.asc())
        else:
            query = query.order_by(Property.created_at.desc(), Property.id.desc())
        query = query.limit(limit + 1)

        rows = list((await self.session.execute(query)).unique().scalars().all())
        has_more = len(rows) > limit
        return rows[:limit], has_more

    async def get_visible(
        self, property_id: UUID, organization_id: UUID, agent_ids: list[UUID] | None
    ) -> Property | None:
        """Fetch one listing the caller is permitted to see under this scope."""
        query = self._visible(organization_id, agent_ids).where(
            Property.id == property_id
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def count_by_status(
        self, organization_id: UUID, agent_ids: list[UUID] | None
    ) -> dict[str, int]:
        """Listing counts per status, for the tab bar."""
        # Scoped inline rather than via `scoped_to_organization`: that helper is
        # typed for Select[Property], and this projects (status, count).
        query = (
            select(Property.status, func.count())
            .select_from(Property)
            .where(Property.organization_id == organization_id)
            .where(Property.deleted_at.is_(None))
            .group_by(Property.status)
        )
        if agent_ids is not None:
            query = query.where(Property.listing_agent_id.in_(agent_ids))

        rows = (await self.session.execute(query)).all()
        return dict(rows)  # type: ignore[arg-type]
