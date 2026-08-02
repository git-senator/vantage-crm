"""Conversation endpoints — the shared inbox, and the inbound webhook.

Two very different kinds of route live here, and the difference is worth being
explicit about.

The `/conversations` routes are ordinary authenticated CRM endpoints: cookie or
bearer auth, RBAC, tenant session, CSRF on writes.

`/conversations/inbound/email` is **not**. A mail provider has no session and no
CSRF cookie; it authenticates with a shared secret over an HMAC of the body. It
therefore gets its own dependency, its own tenant resolution, and a response
that says as little as possible — see `verify_inbound_signature`.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    TenantSessionDep,
    require,
    verify_csrf,
)
from app.api.v1.webhooks import InboundTenantDep
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.schemas.common import Cursor, PageMeta
from app.schemas.conversation import Channel as ChannelLiteral
from app.schemas.conversation import (
    ConversationDetail,
    ConversationFilters,
    ConversationRead,
    ConversationUpdate,
    InboundEmailPayload,
    InboundResult,
    MessageMedia,
    MessageRead,
    MessageSend,
)
from app.services.conversation import ConversationService, InboundMessageService

router = APIRouter()


def _person(user: User | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {
        "id": user.id,
        "full_name": user.full_name,
        "initials": user.initials,
        "avatar_hue": user.avatar_hue,
    }


def _to_conversation(conversation: Conversation) -> ConversationRead:
    return ConversationRead(
        id=conversation.id,
        channel=conversation.channel,
        external_id=conversation.external_id,
        display_name=conversation.display_name,
        subject=conversation.subject,
        entity_type=conversation.entity_type,
        entity_id=conversation.entity_id,
        owner=_person(conversation.owner),
        last_message_at=conversation.last_message_at,
        last_message_preview=conversation.last_message_preview,
        unread_count=conversation.unread_count,
        is_pinned=conversation.is_pinned,
        autopilot=conversation.autopilot,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _to_message(message: Message) -> MessageRead:
    meta = message.metadata_ or {}
    raw_translations = meta.get("translations") or {}
    translations = {
        str(k): str(v)
        for k, v in raw_translations.items()
        if isinstance(v, str) and v
    } if isinstance(raw_translations, dict) else {}
    raw_media = meta.get("media")
    media = (
        [
            MessageMedia(
                kind=item.get("kind", "file"),
                url=str(item["url"]),
                name=item.get("name"),
            )
            for item in raw_media
            if isinstance(item, dict) and item.get("url")
        ]
        if isinstance(raw_media, list)
        else []
    )
    return MessageRead(
        id=message.id,
        conversation_id=message.conversation_id,
        direction=message.direction,
        status=message.status,
        from_address=message.from_address,
        to_address=message.to_address,
        subject=message.subject,
        body_text=message.body_text,
        body_html=message.body_html,
        lang=(meta.get("lang") if isinstance(meta.get("lang"), str) else None),
        translations=translations,
        media=media,
        sender=_person(message.sender),
        sent_at=message.sent_at,
        read_at=message.read_at,
        failure_reason=message.failure_reason,
        created_at=message.created_at,
    )


class ConversationPage(PageMeta):
    pass


@router.get("")
async def list_conversations(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("contacts.view"))],
    _user: CurrentUser,
    channel: Annotated[ChannelLiteral | None, Query()] = None,
    entity_type: Annotated[str | None, Query(max_length=20)] = None,
    entity_id: UUID | None = None,
    unread_only: Annotated[bool, Query()] = False,
    search: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> dict[str, Any]:
    """The inbox, most recently active first.

    Ordered by last activity rather than creation: a two-year-old thread that
    just received a reply belongs at the top, which is the entire point of an
    inbox.
    """
    filters = ConversationFilters(
        channel=channel,
        entity_type=entity_type,
        entity_id=entity_id,
        unread_only=unread_only,
        search=search,
    )
    rows, has_more = await ConversationService(session, auth).list_conversations(
        filters=filters, limit=limit, cursor=Cursor.decode(cursor) if cursor else None
    )
    next_cursor = (
        Cursor(
            created_at=rows[-1].last_message_at or rows[-1].created_at,
            id=rows[-1].id,
        ).encode()
        if rows and has_more
        else None
    )
    return {
        "data": [_to_conversation(row) for row in rows],
        "meta": PageMeta(
            next_cursor=next_cursor, has_more=has_more, limit=limit
        ).model_dump(),
    }


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("contacts.view"))],
    _user: CurrentUser,
) -> ConversationDetail:
    """A thread and its messages, oldest first."""
    service = ConversationService(session, auth)
    conversation = await service.get_conversation(conversation_id)
    messages = await service.list_messages(conversation_id)
    return ConversationDetail(
        conversation=_to_conversation(conversation),
        messages=[_to_message(message) for message in messages],
    )


@router.post(
    "/messages",
    response_model=MessageRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_csrf)],
)
async def send_message(
    payload: MessageSend,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("contacts.manage"))],
    user: CurrentUser,
) -> MessageRead:
    """Queue a message, creating the thread if there is not one already.

    202, not 201: the message is recorded and accepted, and delivery happens
    afterwards. A 201 would claim it had been sent.
    """
    message = await ConversationService(session, auth).send_message(payload, user)
    return _to_message(message)


@router.patch(
    "/{conversation_id}",
    response_model=ConversationRead,
    dependencies=[Depends(verify_csrf)],
)
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("contacts.manage"))],
    user: CurrentUser,
) -> ConversationRead:
    """File a thread against a record, or pin it.

    The manual counterpart to ingestion's automatic match — the answer to "this
    enquiry is actually about the Harrison listing" that no address lookup could
    have found.
    """
    conversation = await ConversationService(session, auth).update_conversation(
        conversation_id, payload, user
    )
    return _to_conversation(conversation)


@router.post(
    "/{conversation_id}/read",
    response_model=ConversationRead,
    dependencies=[Depends(verify_csrf)],
)
async def mark_conversation_read(
    conversation_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("contacts.view"))],
    user: CurrentUser,
) -> ConversationRead:
    """Clear a thread's unread count.

    Read state is per *workspace*: a thread a colleague answered is not still
    unread for everyone else. Notifications are the opposite — they are personal
    — which is why the two track it differently.
    """
    conversation = await ConversationService(session, auth).mark_read(
        conversation_id, user
    )
    return _to_conversation(conversation)


@router.post(
    "/inbound/email",
    response_model=InboundResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_inbound_email(
    payload: InboundEmailPayload,
    request: Request,
    context: InboundTenantDep,
) -> InboundResult:
    """Ingest an inbound email from the mail provider.

    Authenticated by HMAC over the raw body, not by a session — see
    `app/api/v1/webhooks.py`. The response deliberately says almost nothing: a
    body that revealed whether the sender matched a record would turn this
    endpoint into an oracle for enumerating a workspace's contacts.
    """
    session, organization_id = context
    _message, created = await InboundMessageService(
        session, organization_id
    ).ingest_email(payload)
    return InboundResult(status="accepted" if created else "duplicate")
