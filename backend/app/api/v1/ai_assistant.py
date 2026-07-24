"""AI assistant endpoints: conversations and their turns.

Every route is gated by `ai.use` (in the service) and scoped to the calling
user (in the repository), so a conversation is reachable only by the person who
started it, within their organization. The dispatch route translates the two
internal AI failures into HTTP: a budget refusal is a 429 (the limit was
reached, it resets), a provider fault is a 503 (valid request, dependency down,
retry later) — the same posture the rest of the platform takes.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.v1.dependencies import Authorization, CurrentUser, TenantSessionDep
from app.core.exceptions import RateLimitedError, ServiceUnavailableError
from app.schemas.ai_assistant import (
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    MessageRead,
    SendMessage,
)
from app.services.ai.assistant import AssistantService
from app.services.ai.base import AIError, BudgetExceededError

router = APIRouter()


@router.get("/conversations", response_model=list[ConversationRead])
async def list_conversations(
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> list[ConversationRead]:
    """The caller's own conversations, most recently used first."""
    rows = await AssistantService(session, auth).list_conversations(user)
    return [ConversationRead.model_validate(row) for row in rows]


@router.post(
    "/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: ConversationCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ConversationRead:
    """Start a conversation, optionally anchored to a record the caller can see."""
    conversation = await AssistantService(session, auth).create_conversation(
        actor=user,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
    )
    await session.commit()
    return ConversationRead.model_validate(conversation)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ConversationDetail:
    """One conversation and its turns. 404 for anyone but the owner."""
    service = AssistantService(session, auth)
    conversation = await service.get_conversation(conversation_id, user)
    messages = await service.history(conversation_id, user)
    detail = ConversationDetail.model_validate(conversation)
    detail.messages = [MessageRead.model_validate(m) for m in messages]
    return detail


@router.delete(
    "/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_conversation(
    conversation_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await AssistantService(session, auth).delete_conversation(conversation_id, user)
    await session.commit()


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageRead,
)
async def send_message(
    conversation_id: UUID,
    payload: SendMessage,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> MessageRead:
    """Send a message and get the assistant's reply.

    Synchronous: the user is waiting for the answer. The service commits the
    user's turn before dispatch, so a failed reply never loses what they said.
    """
    service = AssistantService(session, auth)
    try:
        reply = await service.send_message(conversation_id, payload.text, user)
    except BudgetExceededError as exc:
        # The monthly ceiling, not a per-second rate — but 429 is the closest
        # honest status, and the detail says when it resets.
        raise RateLimitedError(str(exc), retry_after=3600) from exc
    except AIError as exc:
        # A valid request the model could not answer: the dependency is down or
        # off, not the request malformed. Retryable when the fault was.
        raise ServiceUnavailableError(str(exc)) from exc
    return MessageRead.model_validate(reply)
