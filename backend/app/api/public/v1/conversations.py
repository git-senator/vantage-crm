"""Public conversation endpoints.

The inbound door for the AI lead orchestrator. A client message arrives on any
channel — WhatsApp, a website form, Telegram, Instagram, Facebook, Google,
YouTube — already qualified and translated upstream, and this files it into the
shared inbox so a manager sees it threaded, matched to its lead, and rendered in
their own language.

It is the same `InboundMessageService` the email webhook uses: dedupe, thread,
match, notify are written once and reused. This endpoint adds only the two
things the orchestrator brings that a raw webhook does not — a channel name and
a translation payload.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.public.v1.dependencies import MachinePrincipal, require_scope
from app.schemas.conversation import (
    AiReplyResult,
    BookViewingRequest,
    BookViewingResult,
    InboundAiReply,
    InboundChannelMessage,
    InboundChannelResult,
)
from app.services.conversation import InboundMessageService

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post(
    "/inbound",
    response_model=InboundChannelResult,
    status_code=status.HTTP_201_CREATED,
    summary="File an inbound client message",
)
async def ingest_inbound(
    payload: InboundChannelMessage,
    principal: Annotated[
        MachinePrincipal, Depends(require_scope("contacts.manage"))
    ],
) -> InboundChannelResult:
    service = InboundMessageService(
        principal.session, principal.auth.organization_id
    )
    message, created, autopilot = await service.ingest_translated(
        channel=payload.channel,
        from_address=payload.from_address,
        from_name=payload.from_name,
        to_address=payload.to_address,
        body_text=payload.body_text,
        provider_message_id=payload.provider_message_id,
        subject=payload.subject,
        lang=payload.lang,
        translations=payload.translations,
        media=[item.model_dump() for item in payload.media],
    )
    return InboundChannelResult(
        conversation_id=message.conversation_id,
        message_id=message.id,
        created=created,
        autopilot=autopilot,
    )


@router.post(
    "/reply",
    response_model=AiReplyResult,
    status_code=status.HTTP_201_CREATED,
    summary="File an AI-authored reply into a thread",
)
async def ai_reply(
    payload: InboundAiReply,
    principal: Annotated[
        MachinePrincipal, Depends(require_scope("contacts.manage"))
    ],
) -> AiReplyResult:
    service = InboundMessageService(
        principal.session, principal.auth.organization_id
    )
    message, outcome = await service.post_ai_reply(
        channel=payload.channel,
        to_address=payload.to_address,
        body_text=payload.body_text,
    )
    return AiReplyResult(
        status=outcome,  # type: ignore[arg-type]
        message_id=message.id if message is not None else None,
    )


@router.post(
    "/schedule",
    response_model=BookViewingResult,
    status_code=status.HTTP_201_CREATED,
    summary="Book a property viewing into Google Calendar",
)
async def schedule_viewing(
    payload: BookViewingRequest,
    principal: Annotated[
        MachinePrincipal, Depends(require_scope("contacts.manage"))
    ],
) -> BookViewingResult:
    service = InboundMessageService(
        principal.session, principal.auth.organization_id
    )
    outcome, event = await service.book_viewing(
        channel=payload.channel,
        to_address=payload.to_address,
        summary=payload.summary,
        start=payload.start,
        duration_minutes=payload.duration_minutes,
        description=payload.description,
    )
    return BookViewingResult(
        status=outcome,  # type: ignore[arg-type]
        event_id=event.id if event is not None else None,
        html_link=event.html_link if event is not None else None,
        start=event.start if event is not None else None,
    )
