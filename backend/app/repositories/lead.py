"""Lead data access.

This is the reference for every CRM repository. Three rules it demonstrates:

1. **Scope is a WHERE clause.** `owner_ids` narrows the query itself. Filtering
   after the fetch would return wrong page sizes, leak the existence of records
   the caller cannot see, and scale with the table rather than the result.

2. **Tenant scoping is applied here too**, on top of RLS. Either layer failing
   alone must not leak data.

3. **Keyset pagination**, ordered by `(created_at, id)` to match the index.
   OFFSET is both slow on deep pages and incorrect under concurrent writes.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import joinedload

from app.models.lead import Lead
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE, Cursor
from app.schemas.lead import LeadFilters


class LeadRepository(BaseRepository[Lead]):
    model = Lead

    # ------------------------------------------------------------- queries

    def _visible(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> Select[tuple[Lead]]:
        """Base query: tenant-scoped, RBAC-scoped, soft-deletes excluded.

        `owner_ids is None` means the caller holds ALL scope — no owner
        predicate at all, rather than an IN clause listing every user.
        """
        query = self.scoped_to_organization(self._base_query(), organization_id)
        if owner_ids is not None:
            query = query.where(Lead.owner_id.in_(owner_ids))
        return query.options(joinedload(Lead.owner))

    @staticmethod
    def _apply_filters(
        query: Select[tuple[Lead]], filters: LeadFilters
    ) -> Select[tuple[Lead]]:
        if filters.stage:
            query = query.where(Lead.stage == filters.stage)
        if filters.status:
            query = query.where(Lead.status == filters.status)
        if filters.source:
            query = query.where(Lead.source == filters.source)
        if filters.temperature:
            query = query.where(Lead.temperature == filters.temperature)
        if filters.owner_id:
            # Narrows within the caller's scope; it cannot widen it, because
            # the scope predicate is already applied.
            query = query.where(Lead.owner_id == filters.owner_id)
        if filters.tag:
            # ARRAY containment: does tags contain this exact value. Typed as
            # a literal array so the comparison stays parameterised.
            query = query.where(Lead.tags.contains([filters.tag]))

        if filters.search:
            term = filters.search.strip()
            if term:
                # Full-text over the generated tsvector, OR trigram similarity
                # on the name so a misspelling still finds the record.
                # `websearch_to_tsquery` accepts human input safely — plainto_
                # and to_tsquery raise on characters users type constantly.
                query = query.where(
                    or_(
                        Lead.search_vector.op("@@")(
                            func.websearch_to_tsquery("simple", term)
                        ),
                        func.concat(Lead.first_name, " ", Lead.last_name).op("%")(term),
                    )
                )
        return query

    async def list_page(
        self,
        organization_id: UUID,
        *,
        owner_ids: list[UUID] | None,
        filters: LeadFilters,
        limit: int,
        cursor: Cursor | None = None,
        ascending: bool = False,
    ) -> tuple[list[Lead], bool]:
        """One page, ordered by creation, newest first unless `ascending`.

        Fetches limit+1 to detect a further page without a second COUNT query.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))

        query = self._apply_filters(
            self._visible(organization_id, owner_ids), filters
        )

        if cursor is not None:
            # Row-value comparison, which PostgreSQL can satisfy directly from
            # the (created_at, id) index. Writing it as
            # `created_at < X OR (created_at = X AND id < Y)` is equivalent but
            # the planner handles it far less well. The direction flips with
            # the sort so the cursor keeps walking away from the first page.
            position = func.row(Lead.created_at, Lead.id)
            anchor = func.row(cursor.created_at, cursor.id)
            query = query.where(position > anchor if ascending else position < anchor)

        if ascending:
            query = query.order_by(Lead.created_at.asc(), Lead.id.asc())
        else:
            query = query.order_by(Lead.created_at.desc(), Lead.id.desc())
        query = query.limit(limit + 1)

        rows = list((await self.session.execute(query)).unique().scalars().all())
        has_more = len(rows) > limit
        return rows[:limit], has_more

    async def get_visible(
        self, lead_id: UUID, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> Lead | None:
        """Fetch one lead the caller is permitted to see.

        Scope is applied in the query rather than checked afterwards, so a lead
        outside the caller's scope is indistinguishable from one that does not
        exist — no existence oracle.
        """
        query = self._visible(organization_id, owner_ids).where(Lead.id == lead_id)
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def get_visible_for_update(
        self, lead_id: UUID, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> Lead | None:
        """Same as `get_visible`, but takes a row lock for the transaction.

        Conversion reads the lead, decides it has not been converted, and then
        writes — a check-then-act that two concurrent requests can both pass.
        `FOR UPDATE` serialises them so the second sees the first's write.

        `of=Lead` is not optional. `Lead.owner` is `lazy="joined"`, so every
        query against this model carries a LEFT OUTER JOIN to `users`, and
        PostgreSQL refuses a bare FOR UPDATE on the nullable side of an outer
        join. Locking the leads row specifically is both legal and what we
        actually mean.

        No NOWAIT or SKIP LOCKED: the loser of the race should wait, then
        observe the conversion and return a clean 409, rather than failing with
        a lock error that tells the caller nothing.
        """
        query = (
            self.scoped_to_organization(self._base_query(), organization_id)
            .where(Lead.id == lead_id)
            .with_for_update(of=Lead)
        )
        if owner_ids is not None:
            query = query.where(Lead.owner_id.in_(owner_ids))
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def count_by_stage(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> dict[str, int]:
        """Stage counts for the pipeline summary."""
        # Scoped inline rather than via `scoped_to_organization`: that helper
        # is typed for Select[Lead], and this projects (stage, count).
        query = (
            select(Lead.stage, func.count())
            .select_from(Lead)
            .where(Lead.organization_id == organization_id)
            .where(Lead.deleted_at.is_(None))
            .group_by(Lead.stage)
        )
        if owner_ids is not None:
            query = query.where(Lead.owner_id.in_(owner_ids))

        rows = (await self.session.execute(query)).all()
        return dict(rows)  # type: ignore[arg-type]
