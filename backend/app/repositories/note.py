"""Note data access.

Like `ActivityRepository`, this takes no owner predicate for the per-entity
read: a note's visibility follows the record it hangs off, and the service
proves the caller can read that record before listing. The only owner-scoped
path is the cross-entity feed, whose `author_ids` predicate is the note
equivalent of the activity feed's `actor_ids`.

Ordering is `(created_at DESC, id DESC)` for the feed, but a *record's* notes
lead with pinned rows — the "read me first" context — then newest.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import joinedload

from app.models.note import Note
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE, Cursor
from app.schemas.note import NoteFilters


class NoteRepository(BaseRepository[Note]):
    model = Note

    def _base(self, organization_id: UUID) -> Select[tuple[Note]]:
        return (
            self.scoped_to_organization(self._base_query(), organization_id)
            .options(joinedload(Note.author))
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def _apply_filters(
        query: Select[tuple[Note]], filters: NoteFilters
    ) -> Select[tuple[Note]]:
        if filters.entity_type:
            query = query.where(Note.entity_type == filters.entity_type)
        if filters.entity_id:
            query = query.where(Note.entity_id == filters.entity_id)
        if filters.author_id:
            query = query.where(Note.author_id == filters.author_id)
        if filters.pinned is not None:
            query = query.where(Note.is_pinned.is_(filters.pinned))

        if filters.search:
            term = filters.search.strip()
            if term:
                query = query.where(
                    or_(
                        Note.search_vector.op("@@")(
                            func.websearch_to_tsquery("simple", term)
                        ),
                        func.coalesce(Note.title, "").op("%")(term),
                    )
                )
        return query

    async def list_page(
        self,
        organization_id: UUID,
        *,
        filters: NoteFilters,
        limit: int,
        cursor: Cursor | None = None,
        author_ids: list[UUID] | None = None,
    ) -> tuple[list[Note], bool]:
        """The cross-entity feed. `author_ids` is the scope predicate.

        Ordered by `(created_at, id)` — a feed is chronological. The per-entity
        pinned-first ordering lives in `list_for_entity`, which is a different
        question.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = self._apply_filters(self._base(organization_id), filters)
        if author_ids is not None:
            query = query.where(Note.author_id.in_(author_ids))

        if cursor is not None:
            query = query.where(
                func.row(Note.created_at, Note.id)
                < func.row(cursor.created_at, cursor.id)
            )

        query = query.order_by(Note.created_at.desc(), Note.id.desc()).limit(limit + 1)
        rows = list((await self.session.execute(query)).unique().scalars().all())
        return rows[:limit], len(rows) > limit

    async def list_for_entity(
        self,
        organization_id: UUID,
        *,
        entity_type: str,
        entity_id: UUID,
        limit: int = 50,
    ) -> list[Note]:
        """One record's notes: pinned first, then newest.

        Unpaginated and hard-capped — this backs the detail-page panel and the
        timeline, which show a record's notes in full rather than a page at a
        time.
        """
        query = (
            self._base(organization_id)
            .where(Note.entity_type == entity_type)
            .where(Note.entity_id == entity_id)
            .order_by(Note.is_pinned.desc(), Note.created_at.desc(), Note.id.desc())
            .limit(min(limit, MAX_PAGE_SIZE))
        )
        return list((await self.session.execute(query)).unique().scalars().all())

    async def get(self, entity_id: UUID, organization_id: UUID) -> Note | None:
        query = self._base(organization_id).where(Note.id == entity_id)
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def count_for_entity(
        self, organization_id: UUID, entity_type: str, entity_id: UUID
    ) -> int:
        query = (
            select(func.count())
            .select_from(Note)
            .where(Note.organization_id == organization_id)
            .where(Note.entity_type == entity_type)
            .where(Note.entity_id == entity_id)
            .where(Note.deleted_at.is_(None))
        )
        return int((await self.session.execute(query)).scalar() or 0)
