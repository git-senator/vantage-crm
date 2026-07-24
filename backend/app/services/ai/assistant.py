"""AssistantService — the CRM assistant's orchestration.

This is where a user's message becomes an answer, and it is deliberately the
*only* place that composition happens. It owns conversation lifecycle (create,
list, read, delete) and the turn loop (persist the user's message, assemble a
scoped prompt, dispatch through `AIService`, persist the reply).

It composes; it does not talk to a model. Every completion goes through
`AIService.complete`, so the enablement check, the `ai.use` permission, the cost
ceiling before dispatch, the ledger and the egress audit all hold here exactly
as everywhere else. The assistant never imports a provider and could not reach
one if it tried.

Three things worth stating because they are where the safety lives:

  * **The composed prompt is never stored.** Each turn re-fetches the anchored
    entity's context under the user's scope, redacts and fences it, and folds it
    into the request — and none of that assembled prompt is persisted. Only the
    user's message and the assistant's reply land in `ai_messages`.
  * **Context is re-fetched every turn, under scope.** Data changes between
    turns, and access can be revoked between turns; rebuilding from the scoped
    service each time is both fresher and safer than caching a snapshot.
  * **A failed reply still records.** If the model errors or the budget refuses,
    the user's turn and the ledger row are committed before the error is raised,
    so the conversation is not silently lost and the attempt is still accounted
    for.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

# Registers the assistant prompt in PROMPTS at import.
from app.ai import assistant_prompt as _assistant_prompt
from app.ai.context import instruction
from app.ai.prompts import ContentBlock
from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.ai_conversation import (
    AI_CONVERSATION_ENTITIES,
    AiConversation,
    AiMessage,
)
from app.models.user import User
from app.repositories.ai_conversation import AiConversationRepository
from app.services.ai.base import AIError, ChatMessage
from app.services.ai.context_builders import build_entity_context
from app.services.ai.service import AIService
from app.services.rbac import AuthorizationContext

logger = get_logger(__name__)

FEATURE = "assistant"

#: How many prior turns are fed back to the model. A cap, not the whole history:
#: an unbounded transcript is an unbounded prompt and an unbounded bill, and the
#: last dozen turns carry the thread of a working conversation. Older turns stay
#: in the database and on the user's screen; they are just not re-sent.
HISTORY_TURNS = 12

#: A user message longer than this is refused. Not a model limit — a guard
#: against a paste of an entire document becoming one very expensive prompt.
MAX_MESSAGE_CHARS = 8000


class AssistantService:
    def __init__(
        self,
        session: AsyncSession,
        auth: AuthorizationContext,
        *,
        ai: AIService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings or get_settings()
        self.repo = AiConversationRepository(session)
        # Injectable so a test drives the whole assistant against the echo
        # provider; defaults to a service that resolves the configured provider.
        self.ai = ai or AIService(session, auth, settings=self.settings)

    # -------------------------------------------------------- lifecycle

    async def create_conversation(
        self,
        *,
        actor: User,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
    ) -> AiConversation:
        """Start a conversation, optionally anchored to a record.

        `ai.use` is required to have an assistant at all. If an anchor is given,
        it is validated **under the user's scope** — a conversation cannot be
        anchored to a record the user cannot see, so the anchor can never become
        a back door to context they lack.
        """
        self.auth.require("ai.use")

        if (entity_type is None) != (entity_id is None):
            raise ConflictError("An anchor needs both an entity type and id, or neither.")

        if entity_type is not None and entity_id is not None:
            if entity_type not in AI_CONVERSATION_ENTITIES:
                raise ConflictError(f"Cannot anchor a conversation to '{entity_type}'.")
            context = await build_entity_context(
                self.session, self.auth, entity_type, entity_id
            )
            if context is None:
                # Indistinguishable from "does not exist" — the same 404 the
                # entity's own endpoint would give, so anchoring cannot probe.
                raise NotFoundError("That record was not found.")

        conversation = AiConversation(
            organization_id=self.auth.organization_id,
            user_id=actor.id,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def list_conversations(self, actor: User) -> list[AiConversation]:
        self.auth.require("ai.use")
        return await self.repo.list_for_user(self.auth.organization_id, actor.id)

    async def get_conversation(
        self, conversation_id: UUID, actor: User
    ) -> AiConversation:
        self.auth.require("ai.use")
        conversation = await self.repo.get_for_user(
            conversation_id, self.auth.organization_id, actor.id
        )
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        return conversation

    async def history(
        self, conversation_id: UUID, actor: User
    ) -> list[AiMessage]:
        # Ownership is enforced by get_conversation before any turn is read.
        await self.get_conversation(conversation_id, actor)
        return await self.repo.messages(conversation_id)

    async def delete_conversation(self, conversation_id: UUID, actor: User) -> None:
        conversation = await self.get_conversation(conversation_id, actor)
        conversation.deleted_at = datetime.now(UTC)
        await self.session.flush()

    # ------------------------------------------------------------- turn

    async def send_message(
        self, conversation_id: UUID, text: str, actor: User
    ) -> AiMessage:
        """One turn: persist the user's message, answer it, persist the reply.

        Returns the assistant's message. Raises `AIError`/`BudgetExceededError`
        when the model or the budget refuses — but only after the user's turn and
        the ledger row are committed, so nothing is silently lost.
        """
        self.auth.require("ai.use")

        message = text.strip()
        if not message:
            raise ConflictError("A message cannot be empty.")
        if len(message) > MAX_MESSAGE_CHARS:
            raise ConflictError(
                f"That message is too long ({len(message)} characters); "
                f"the limit is {MAX_MESSAGE_CHARS}."
            )

        conversation = await self.get_conversation(conversation_id, actor)

        # Persist the user's turn and commit it first: whatever happens to the
        # reply, the user said this and it is theirs to keep.
        user_turn = AiMessage(
            organization_id=self.auth.organization_id,
            conversation_id=conversation.id,
            role="user",
            content=message,
        )
        self.session.add(user_turn)
        if conversation.title is None:
            conversation.title = _derive_title(message)
        await self.session.commit()

        request = await self._compose(conversation, message)

        try:
            result = await self.ai.complete(request, feature=FEATURE, actor=actor)
        except AIError:
            # The failed/refused ledger row and audit entry were written inside
            # `complete`; commit them so the attempt is recorded, then let the
            # error surface. The user's turn is already saved.
            await self.session.commit()
            raise

        assistant_turn = AiMessage(
            organization_id=self.auth.organization_id,
            conversation_id=conversation.id,
            role="assistant",
            content=result.text or "(no response)",
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            ai_job_id=self.ai.last_job.id if self.ai.last_job is not None else None,
            truncated=result.truncated,
        )
        self.session.add(assistant_turn)
        await self.repo.touch(conversation, datetime.now(UTC))
        await self.session.commit()

        logger.info(
            "assistant_turn",
            extra={
                "conversation_id": str(conversation.id),
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
            },
        )
        return assistant_turn

    # --------------------------------------------------------- compose

    async def _compose(self, conversation: AiConversation, message: str):  # type: ignore[no-untyped-def]
        """Assemble the request for this turn. The composed prompt is not stored.

        The latest user turn is the entity context (rebuilt under scope, fresh
        every turn) followed by the user's message. Prior turns travel as
        history — their plain text, without re-injected context, so a long
        conversation does not re-send the same record a dozen times.
        """
        blocks: list[ContentBlock] = []

        if conversation.entity_type is not None and conversation.entity_id is not None:
            context = await build_entity_context(
                self.session,
                self.auth,
                conversation.entity_type,
                conversation.entity_id,
            )
            if context is not None:
                # Access may have been revoked since the conversation was
                # anchored; if so, context is None and the turn simply proceeds
                # without it rather than leaking that the record still exists.
                blocks.extend(context)

        # The user's own message is a trusted instruction — they are driving
        # their own assistant, not supplying attacker-influenced CRM data.
        blocks.append(instruction(message))

        history = await self._history_messages(conversation)

        return _assistant_prompt.ASSISTANT_PROMPT.build(
            blocks,
            model=self.settings.AI_MODEL,
            max_tokens=self.settings.AI_MAX_OUTPUT_TOKENS,
            history=history,
            metadata={"conversation": str(conversation.id)},
        )

    async def _history_messages(
        self, conversation: AiConversation
    ) -> list[ChatMessage]:
        """The last few turns, as provider messages, excluding the turn we just
        stored (it becomes the new user block, not history)."""
        turns = await self.repo.messages(conversation.id, limit=HISTORY_TURNS + 1)
        # Drop the final user turn — it is the message we are answering and is
        # composed as the latest block, so including it here would duplicate it.
        prior = turns[:-1] if turns and turns[-1].role == "user" else turns
        return [
            ChatMessage(role=turn.role, content=turn.content)  # type: ignore[arg-type]
            for turn in prior
        ]


def _derive_title(message: str) -> str:
    """A short label from the opening message. No model call — a title is not
    worth a completion, and the first line is what a user recognises anyway."""
    first_line = message.strip().splitlines()[0] if message.strip() else "New chat"
    return first_line[:80]


__all__ = ["FEATURE", "HISTORY_TURNS", "MAX_MESSAGE_CHARS", "AssistantService"]
