"""Activity data access.

Unlike every other CRM repository, this one takes no owner predicate.

An activity's visibility follows **the record it hangs off**, not who logged
it. An agent must see every activity on their own lead, whoever wrote it —
scoping by `actor_id` would put holes in their own timeline. The service is
therefore responsible for proving the caller can read the parent entity before
calling `list_for_entity`, and the only actor-scoped path is the global feed,
which genuinely means "what have I been doing".

Ordering is `(occurred_at DESC, id DESC)` rather than `created_at`, because a
call logged on Monday may have happened on Friday and belongs where it
happened. Keyset pagination follows the same key.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import joinedload

from app.models.activity import Activity
from app.repositories.base import BaseRepository
from app.schemas.activity import ActivityFilters
from app.schemas.common import MAX_PAGE_SIZE


class ActivityCursor:
    """Keyset position for a timeline.

    Separate from `schemas.common.Cursor` because that one encodes
    `created_at`, and a timeline orders by `occurred_at`. Sharing it would
    silently order by the wrong column.
    """

    __slots__ = ("id", "occurred_at")

    def __init__(self, occurred_at: datetime, id: UUID) -> None:
        self.occurred_at = occurred_at
        self.id = id

    def encode(self) -> str:
        import base64
        import json

        payload = json.dumps(
            {"o": self.occurred_at.isoformat(), "i": str(self.id)},
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> ActivityCursor | None:
        """A bad cursor yields the first page rather than a 400."""
        import base64
        import binascii
        import json

        try:
            padded = token + "=" * (-len(token) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded))
            return cls(datetime.fromisoformat(data["o"]), UUID(data["i"]))
        except (ValueError, KeyError, TypeError, binascii.Error):
            return None


class ActivityRepository(BaseRepository[Activity]):
    model = Activity

    def _base(self, organization_id: UUID) -> Select[tuple[Activity]]:
        return (
            self.scoped_to_organization(select(Activity), organization_id)
            .options(joinedload(Activity.actor))
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def _apply_filters(
        query: Select[tuple[Activity]], filters: ActivityFilters
    ) -> Select[tuple[Activity]]:
        if filters.type:
            query = query.where(Activity.type == filters.type)
        if filters.entity_type:
            query = query.where(Activity.entity_type == filters.entity_type)
        if filters.entity_id:
            query = query.where(Activity.entity_id == filters.entity_id)
        if filters.actor_id:
            query = query.where(Activity.actor_id == filters.actor_id)
        if filters.occurred_before:
            query = query.where(Activity.occurred_at <= filters.occurred_before)
        if filters.occurred_after:
            query = query.where(Activity.occurred_at >= filters.occurred_after)

        if filters.search:
            term = filters.search.strip()
            if term:
                query = query.where(
                    or_(
                        Activity.search_vector.op("@@")(
                            func.websearch_to_tsquery("simple", term)
                        ),
                        Activity.subject.op("%")(term),
                    )
                )
        return query

    async def list_page(
        self,
        organization_id: UUID,
        *,
        filters: ActivityFilters,
        limit: int,
        cursor: ActivityCursor | None = None,
        actor_ids: list[UUID] | None = None,
    ) -> tuple[list[Activity], bool]:
        """`actor_ids` is the feed's scope predicate — None means no restriction.

        Applied here rather than by filtering the returned page, because a
        post-fetch filter returns short pages and makes `has_more` a lie.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = self._apply_filters(self._base(organization_id), filters)
        if actor_ids is not None:
            query = query.where(Activity.actor_id.in_(actor_ids))

        if cursor is not None:
            query = query.where(
                func.row(Activity.occurred_at, Activity.id)
                < func.row(cursor.occurred_at, cursor.id)
            )

        query = query.order_by(
            Activity.occurred_at.desc(), Activity.id.desc()
        ).limit(limit + 1)

        rows = list((await self.session.execute(query)).unique().scalars().all())
        return rows[:limit], len(rows) > limit

    async def get(self, entity_id: UUID, organization_id: UUID) -> Activity | None:
        query = self._base(organization_id).where(Activity.id == entity_id)
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def count_for_entity(
        self, organization_id: UUID, entity_type: str, entity_id: UUID
    ) -> int:
        query = (
            select(func.count())
            .select_from(Activity)
            .where(Activity.organization_id == organization_id)
            .where(Activity.entity_type == entity_type)
            .where(Activity.entity_id == entity_id)
        )
        return int((await self.session.execute(query)).scalar() or 0)
