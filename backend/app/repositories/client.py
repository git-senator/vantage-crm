"""Client data access.

Follows the LeadRepository pattern exactly — see `app/repositories/lead.py` for
why scope is a WHERE clause, why tenant scoping is applied here as well as by
RLS, and why pagination is keyset rather than OFFSET.

Nothing here is novel. The one new query this slice needed —
`get_visible_for_update`, which locks a lead during conversion — belongs on
`LeadRepository`, because it is a lead that gets locked.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import joinedload

from app.models.client import Client
from app.repositories.base import BaseRepository
from app.schemas.client import ClientFilters
from app.schemas.common import MAX_PAGE_SIZE, Cursor


class ClientRepository(BaseRepository[Client]):
    model = Client

    # ------------------------------------------------------------- queries

    def _visible(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> Select[tuple[Client]]:
        """Base query: tenant-scoped, RBAC-scoped, soft-deletes excluded.

        `owner_ids is None` means the caller holds ALL scope — no owner
        predicate at all, rather than an IN clause listing every user.
        """
        query = self.scoped_to_organization(self._base_query(), organization_id)
        if owner_ids is not None:
            query = query.where(Client.owner_id.in_(owner_ids))
        return query.options(joinedload(Client.owner))

    @staticmethod
    def _apply_filters(
        query: Select[tuple[Client]], filters: ClientFilters
    ) -> Select[tuple[Client]]:
        if filters.type:
            query = query.where(Client.type == filters.type)
        if filters.status:
            query = query.where(Client.status == filters.status)
        if filters.owner_id:
            # Narrows within the caller's scope; it cannot widen it, because
            # the scope predicate is already applied.
            query = query.where(Client.owner_id == filters.owner_id)
        if filters.tag:
            query = query.where(Client.tags.contains([filters.tag]))

        if filters.search:
            term = filters.search.strip()
            if term:
                # Full-text over the generated tsvector, OR trigram similarity
                # on the name. `websearch_to_tsquery` accepts human input
                # safely — to_tsquery raises on characters users type
                # constantly.
                #
                # The trigram expression must match ix_clients_name_trgm
                # exactly, coalesce included, or the index is not used.
                name_expression = func.concat(
                    func.coalesce(Client.company_name, ""),
                    " ",
                    func.coalesce(Client.first_name, ""),
                    " ",
                    func.coalesce(Client.last_name, ""),
                )
                query = query.where(
                    or_(
                        Client.search_vector.op("@@")(
                            func.websearch_to_tsquery("simple", term)
                        ),
                        name_expression.op("%")(term),
                    )
                )
        return query

    async def list_page(
        self,
        organization_id: UUID,
        *,
        owner_ids: list[UUID] | None,
        filters: ClientFilters,
        limit: int,
        cursor: Cursor | None = None,
    ) -> tuple[list[Client], bool]:
        """One page, newest first. Returns `(rows, has_more)`.

        Fetches limit+1 to detect a further page without a second COUNT query.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))

        query = self._apply_filters(self._visible(organization_id, owner_ids), filters)

        if cursor is not None:
            # Row-value comparison, which PostgreSQL can satisfy directly from
            # the (created_at, id) index.
            query = query.where(
                func.row(Client.created_at, Client.id)
                < func.row(cursor.created_at, cursor.id)
            )

        query = query.order_by(Client.created_at.desc(), Client.id.desc()).limit(
            limit + 1
        )

        rows = list((await self.session.execute(query)).unique().scalars().all())
        has_more = len(rows) > limit
        return rows[:limit], has_more

    async def get_visible(
        self, client_id: UUID, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> Client | None:
        """Fetch one client the caller is permitted to see.

        Scope is applied in the query rather than checked afterwards, so a
        client outside the caller's scope is indistinguishable from one that
        does not exist — no existence oracle.
        """
        query = self._visible(organization_id, owner_ids).where(Client.id == client_id)
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def count_by_type(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> dict[str, int]:
        """Client counts per type, for the tab bar."""
        # Scoped inline rather than via `scoped_to_organization`: that helper is
        # typed for Select[Client], and this projects (type, count).
        query = (
            select(Client.type, func.count())
            .select_from(Client)
            .where(Client.organization_id == organization_id)
            .where(Client.deleted_at.is_(None))
            .group_by(Client.type)
        )
        if owner_ids is not None:
            query = query.where(Client.owner_id.in_(owner_ids))

        rows = (await self.session.execute(query)).all()
        return dict(rows)  # type: ignore[arg-type]
