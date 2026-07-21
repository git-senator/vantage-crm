"""Deal data access.

Follows the LeadRepository pattern — see `app/repositories/lead.py` for why
scope is a WHERE clause, why tenant scoping is applied here as well as by RLS,
and why pagination is keyset rather than OFFSET.

Deals are a personal book of business, like leads and clients and unlike
properties: an agent holds `deals.view` at OWN scope, so the owner predicate is
live for them and a deal outside scope is a 404.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import joinedload

from app.models.deal import Deal, DealStageHistory
from app.models.pipeline import PipelineStage
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE, Cursor
from app.schemas.deal import DealFilters


class DealRepository(BaseRepository[Deal]):
    model = Deal

    # ------------------------------------------------------------- queries

    def _visible(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> Select[tuple[Deal]]:
        """Base query: tenant-scoped, RBAC-scoped, soft-deletes excluded."""
        query = self.scoped_to_organization(self._base_query(), organization_id)
        if owner_ids is not None:
            query = query.where(Deal.owner_id.in_(owner_ids))
        # The list view renders all four, so loading them here avoids an N+1
        # that would otherwise fire once per row during serialisation.
        #
        # `populate_existing` is load-bearing, not a tuning knob. `Deal.status`
        # is derived by reading through `Deal.stage`; after a transition
        # updates `stage_id` and re-reads the deal, SQLAlchemy returns the
        # instance already in the identity map with its *old* stage still
        # attached — so the response reports the pre-move stage and a stale
        # status, and the Kanban card snaps back. Overwriting loaded state on
        # every read is what makes a read-after-write correct.
        return query.options(
            joinedload(Deal.owner),
            joinedload(Deal.stage),
            joinedload(Deal.client),
            joinedload(Deal.listing),
        ).execution_options(populate_existing=True)

    @staticmethod
    def _apply_filters(
        query: Select[tuple[Deal]], filters: DealFilters
    ) -> Select[tuple[Deal]]:
        if filters.pipeline_id:
            query = query.where(Deal.pipeline_id == filters.pipeline_id)
        if filters.stage_id:
            query = query.where(Deal.stage_id == filters.stage_id)
        if filters.owner_id:
            query = query.where(Deal.owner_id == filters.owner_id)
        if filters.client_id:
            query = query.where(Deal.client_id == filters.client_id)
        if filters.property_id:
            query = query.where(Deal.property_id == filters.property_id)
        if filters.priority:
            query = query.where(Deal.priority == filters.priority)
        if filters.min_value is not None:
            query = query.where(Deal.value >= filters.min_value)
        if filters.max_value is not None:
            query = query.where(Deal.value <= filters.max_value)
        if filters.expected_close_before is not None:
            query = query.where(
                Deal.expected_close_date <= filters.expected_close_before
            )
        if filters.expected_close_after is not None:
            query = query.where(
                Deal.expected_close_date >= filters.expected_close_after
            )

        if filters.status:
            # `status` is derived from the stage, so filtering on it means
            # correlating to pipeline_stages rather than comparing a column.
            # EXISTS rather than a join: a join would need de-duplicating
            # against the joinedloads above.
            terminal = select(PipelineStage.id).where(
                PipelineStage.id == Deal.stage_id
            )
            if filters.status == "won":
                query = query.where(terminal.where(PipelineStage.is_won).exists())
            elif filters.status == "lost":
                query = query.where(terminal.where(PipelineStage.is_lost).exists())
            else:
                query = query.where(
                    terminal.where(
                        ~PipelineStage.is_won, ~PipelineStage.is_lost
                    ).exists()
                )

        if filters.search:
            term = filters.search.strip()
            if term:
                query = query.where(
                    or_(
                        Deal.search_vector.op("@@")(
                            func.websearch_to_tsquery("simple", term)
                        ),
                        Deal.title.op("%")(term),
                    )
                )
        return query

    async def list_page(
        self,
        organization_id: UUID,
        *,
        owner_ids: list[UUID] | None,
        filters: DealFilters,
        limit: int,
        cursor: Cursor | None = None,
    ) -> tuple[list[Deal], bool]:
        """One page, newest first. Returns `(rows, has_more)`."""
        limit = max(1, min(limit, MAX_PAGE_SIZE))

        query = self._apply_filters(self._visible(organization_id, owner_ids), filters)

        if cursor is not None:
            query = query.where(
                func.row(Deal.created_at, Deal.id)
                < func.row(cursor.created_at, cursor.id)
            )

        query = query.order_by(Deal.created_at.desc(), Deal.id.desc()).limit(limit + 1)

        rows = list((await self.session.execute(query)).unique().scalars().all())
        has_more = len(rows) > limit
        return rows[:limit], has_more

    async def get_visible(
        self, deal_id: UUID, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> Deal | None:
        """Fetch one deal the caller is permitted to see."""
        query = self._visible(organization_id, owner_ids).where(Deal.id == deal_id)
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def get_visible_for_update(
        self, deal_id: UUID, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> Deal | None:
        """Same, but takes a row lock for the transaction.

        Stage transitions read the current stage, compute a duration from the
        last history row, then write — a check-then-act two concurrent drags of
        the same card would both pass, producing two history rows claiming to
        leave the same stage.

        `of=Deal` because the relationships are `lazy="joined"`: PostgreSQL
        refuses a bare FOR UPDATE on the nullable side of an outer join, and
        `owner` and `listing` are both nullable.
        """
        query = (
            self.scoped_to_organization(self._base_query(), organization_id)
            .where(Deal.id == deal_id)
            .with_for_update(of=Deal)
        )
        if owner_ids is not None:
            query = query.where(Deal.owner_id.in_(owner_ids))
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def list_for_board(
        self,
        organization_id: UUID,
        *,
        owner_ids: list[UUID] | None,
        pipeline_id: UUID,
        filters: DealFilters,
    ) -> list[Deal]:
        """Every visible deal in one pipeline, for the Kanban board.

        Deliberately unpaginated but hard-capped: a board is only useful when
        every card is on it, and a pipeline with more deals than the cap is one
        nobody can read anyway. The cap stops a large tenant turning a board
        render into an unbounded query.
        """
        query = self._apply_filters(self._visible(organization_id, owner_ids), filters)
        query = query.where(Deal.pipeline_id == pipeline_id)
        query = query.order_by(Deal.created_at.desc(), Deal.id.desc()).limit(
            BOARD_DEAL_CAP
        )
        return list((await self.session.execute(query)).unique().scalars().all())

    async def count_open_in_stage(
        self, stage_id: UUID, organization_id: UUID
    ) -> int:
        """How many live deals a stage still holds.

        Used before retiring a stage: `ON DELETE RESTRICT` would refuse the
        delete anyway, but a 409 explaining "12 deals are still here" is more
        use than a foreign-key error.
        """
        query = (
            select(func.count())
            .select_from(Deal)
            .where(Deal.stage_id == stage_id)
            .where(Deal.organization_id == organization_id)
            .where(Deal.deleted_at.is_(None))
        )
        return int((await self.session.execute(query)).scalar() or 0)


#: Upper bound on a single board render. Well above any realistic pipeline;
#: low enough that it cannot become an accidental full-table scan.
BOARD_DEAL_CAP = 500


class DealStageHistoryRepository(BaseRepository[DealStageHistory]):
    model = DealStageHistory

    async def latest_for_deal(
        self, deal_id: UUID, organization_id: UUID
    ) -> DealStageHistory | None:
        """The most recent transition, used to compute time-in-stage."""
        query = (
            select(DealStageHistory)
            .where(DealStageHistory.deal_id == deal_id)
            .where(DealStageHistory.organization_id == organization_id)
            .order_by(DealStageHistory.changed_at.desc())
            .limit(1)
        )
        return (await self.session.execute(query)).unique().scalars().first()

    async def list_for_deal(
        self, deal_id: UUID, organization_id: UUID, limit: int = 100
    ) -> list[DealStageHistory]:
        """One deal's history, newest first."""
        query = (
            select(DealStageHistory)
            .where(DealStageHistory.deal_id == deal_id)
            .where(DealStageHistory.organization_id == organization_id)
            .order_by(DealStageHistory.changed_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).unique().scalars().all())
