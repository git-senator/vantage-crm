"""Data access for assistant conversations.

Every method takes `user_id` and filters on it. That is not an optimisation — it
is the privacy boundary. RLS keeps one organization's conversations out of
another's; this repository keeps one *user's* conversations out of their
colleagues'. A conversation is never fetched by id alone, so there is no path
that returns a row belonging to a different user in the same org.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_conversation import AiConversation, AiMessage


class AiConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(
        self, organization_id: UUID, user_id: UUID, *, limit: int = 50
    ) -> list[AiConversation]:
        query = (
            select(AiConversation)
            .where(AiConversation.organization_id == organization_id)
            .where(AiConversation.user_id == user_id)
            .where(AiConversation.deleted_at.is_(None))
            # Most recently used first, falling back to creation for a
            # conversation that has no turns yet.
            .order_by(
                func.coalesce(
                    AiConversation.last_message_at, AiConversation.created_at
                ).desc()
            )
            .limit(limit)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def get_for_user(
        self, conversation_id: UUID, organization_id: UUID, user_id: UUID
    ) -> AiConversation | None:
        """One conversation, only if it belongs to this user.

        Returns `None` for someone else's conversation exactly as for one that
        does not exist — the caller turns both into a 404, so a probe cannot
        distinguish "not yours" from "not real".
        """
        query = (
            select(AiConversation)
            .where(AiConversation.id == conversation_id)
            .where(AiConversation.organization_id == organization_id)
            .where(AiConversation.user_id == user_id)
            .where(AiConversation.deleted_at.is_(None))
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def messages(
        self, conversation_id: UUID, *, limit: int | None = None
    ) -> list[AiMessage]:
        """A conversation's turns, oldest first.

        The conversation's ownership is checked by `get_for_user` before this is
        called, so this reads by conversation id alone — it is never a public
        entry point.
        """
        query = (
            select(AiMessage)
            .where(AiMessage.conversation_id == conversation_id)
            .order_by(AiMessage.created_at.asc())
        )
        if limit is not None:
            # For history fed back to the model, the *most recent* N turns are
            # what matter, so take the tail and re-sort to chronological.
            query = (
                select(AiMessage)
                .where(AiMessage.conversation_id == conversation_id)
                .order_by(AiMessage.created_at.desc())
                .limit(limit)
            )
            rows = list((await self.session.execute(query)).scalars().all())
            return list(reversed(rows))
        return list((await self.session.execute(query)).scalars().all())

    async def touch(self, conversation: AiConversation, when: datetime) -> None:
        conversation.last_message_at = when


__all__ = ["AiConversationRepository"]
