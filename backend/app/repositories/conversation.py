"""Conversation and message data access.

A conversation's scope anchor is `owner_id`, like a lead's — so the same
own/team/all machinery applies and there is no fourth idea of visibility in the
codebase. The one deviation: an **unowned** conversation (nobody has claimed the
inbound thread yet) is visible to everyone in the tenant. Hiding unclaimed mail
from everybody is how an enquiry sits unanswered for a week.

Messages carry no scope of their own. They are read through their conversation,
which the service proves first — the same rule notes and activities follow.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import joinedload

from app.models.conversation import Conversation, Message
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE, Cursor
from app.schemas.conversation import ConversationFilters


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation

    def _base(self, organization_id: UUID) -> Select[tuple[Conversation]]:
        return (
            self.scoped_to_organization(self._base_query(), organization_id)
            .options(joinedload(Conversation.owner))
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def _scoped(
        query: Select[tuple[Conversation]], owner_ids: list[UUID] | None
    ) -> Select[tuple[Conversation]]:
        """Own/team/all, plus unclaimed threads.

        `owner_ids is None` means ALL scope and adds no predicate. Otherwise the
        caller sees their own threads **and** the ones nobody owns — unclaimed
        inbound mail belongs to whoever picks it up, and making it invisible
        until someone claims it means nobody ever does.
        """
        if owner_ids is None:
            return query
        return query.where(
            or_(
                Conversation.owner_id.in_(owner_ids),
                Conversation.owner_id.is_(None),
            )
        )

    @staticmethod
    def _apply_filters(
        query: Select[tuple[Conversation]], filters: ConversationFilters
    ) -> Select[tuple[Conversation]]:
        if filters.channel:
            query = query.where(Conversation.channel == filters.channel)
        if filters.entity_type:
            query = query.where(Conversation.entity_type == filters.entity_type)
        if filters.entity_id:
            query = query.where(Conversation.entity_id == filters.entity_id)
        if filters.unread_only:
            query = query.where(Conversation.unread_count > 0)
        if filters.search:
            term = f"%{filters.search.strip()}%"
            # ILIKE rather than a search vector: an inbox is searched by who and
            # about what, over a few hundred rows per tenant, and a tsvector
            # here would be maintenance for no measurable gain.
            query = query.where(
                or_(
                    Conversation.display_name.ilike(term),
                    Conversation.external_id.ilike(term),
                    Conversation.subject.ilike(term),
                )
            )
        return query

    async def list_page(
        self,
        organization_id: UUID,
        *,
        filters: ConversationFilters,
        owner_ids: list[UUID] | None,
        limit: int,
        cursor: Cursor | None = None,
    ) -> tuple[list[Conversation], bool]:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = self._apply_filters(
            self._scoped(self._base(organization_id), owner_ids), filters
        )

        if cursor is not None:
            # Ordered by last activity, so that is the keyset. `created_at` is
            # the wrong axis for an inbox: a two-year-old thread that just
            # replied belongs at the top.
            query = query.where(
                func.row(
                    func.coalesce(Conversation.last_message_at, Conversation.created_at),
                    Conversation.id,
                )
                < func.row(cursor.created_at, cursor.id)
            )

        query = query.order_by(
            func.coalesce(
                Conversation.last_message_at, Conversation.created_at
            ).desc(),
            Conversation.id.desc(),
        ).limit(limit + 1)

        rows = list((await self.session.execute(query)).unique().scalars().all())
        return rows[:limit], len(rows) > limit

    async def get_visible(
        self, conversation_id: UUID, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> Conversation | None:
        query = self._scoped(
            self._base(organization_id).where(Conversation.id == conversation_id),
            owner_ids,
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def find_by_identity(
        self, organization_id: UUID, *, channel: str, external_id: str
    ) -> Conversation | None:
        """The thread for this person on this channel, if it exists.

        Unscoped by owner deliberately: this is the ingestion path deciding
        whether to append or create, and creating a second thread because the
        first belongs to a colleague is exactly the fragmentation the unique
        index exists to prevent.
        """
        query = self._base(organization_id).where(
            Conversation.channel == channel, Conversation.external_id == external_id
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def total_unread(
        self, organization_id: UUID, owner_ids: list[UUID] | None
    ) -> int:
        query = (
            select(func.coalesce(func.sum(Conversation.unread_count), 0))
            .select_from(Conversation)
            .where(Conversation.organization_id == organization_id)
            .where(Conversation.deleted_at.is_(None))
        )
        # Same predicate as `_scoped`, spelled out because that helper is typed
        # for entity selects and this is an aggregate.
        if owner_ids is not None:
            query = query.where(
                or_(
                    Conversation.owner_id.in_(owner_ids),
                    Conversation.owner_id.is_(None),
                )
            )
        return int((await self.session.execute(query)).scalar_one() or 0)


class MessageRepository(BaseRepository[Message]):
    model = Message

    async def list_for_conversation(
        self, conversation_id: UUID, organization_id: UUID, *, limit: int = 100
    ) -> list[Message]:
        """A thread, oldest first — the order it is read in."""
        query = (
            self.scoped_to_organization(self._base_query(), organization_id)
            .where(Message.conversation_id == conversation_id)
            .options(joinedload(Message.sender))
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(min(limit, MAX_PAGE_SIZE))
            .execution_options(populate_existing=True)
        )
        return list((await self.session.execute(query)).unique().scalars().all())

    async def find_by_provider_id(
        self, organization_id: UUID, provider_message_id: str
    ) -> Message | None:
        """Deduplication. Every provider replays webhooks eventually."""
        query = (
            select(Message)
            .where(Message.organization_id == organization_id)
            .where(Message.provider_message_id == provider_message_id)
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()


    async def latest_threadable(
        self, conversation_id: UUID, organization_id: UUID
    ) -> Message | None:
        """The most recent message in the thread that carries a Message-ID.

        The parent a reply should point at. Rows without an id are skipped
        rather than treated as the parent: a WhatsApp message, or an email that
        failed before it was ever assigned one, cannot anchor a chain, and
        referencing nothing is better than referencing a gap.
        """
        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.organization_id == organization_id)
            .where(Message.rfc_message_id.is_not(None))
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(query)).scalars().first()
