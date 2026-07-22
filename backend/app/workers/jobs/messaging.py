"""Outbound message delivery.

The message row already exists, written in the request's transaction with
`status='queued'`. This job's only job is to hand it to a channel and record
what happened — which means a provider outage shows up in the thread as a failed
message the sender can see and retry, rather than as a request that 500'd and
lost what they typed.

Retries come from `@job`. A terminal provider rejection — a malformed or
suppressed address — is not retried, because five more attempts will produce
five more rejections and, for email, five more marks against sender reputation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core.logging import get_logger
from app.models.conversation import Conversation, Message
from app.services.messaging import (
    MessagingError,
    OutboundMessage,
    build_channel,
)
from app.workers.context import tenant_scope
from app.workers.runner import job

logger = get_logger(__name__)


@job(organization_arg=1)
async def deliver_message(
    ctx: dict[str, Any], message_id: str, organization_id: str
) -> str:
    """Send one queued message. Returns its resulting status."""
    organization = UUID(organization_id)

    async with tenant_scope(organization) as session:
        message = (
            (
                await session.execute(
                    select(Message)
                    .where(Message.id == UUID(message_id))
                    .where(Message.organization_id == organization)
                )
            )
            .unique()
            .scalar_one_or_none()
        )
        if message is None or message.deleted_at is not None:
            return "gone"
        if message.status != "queued":
            # Already delivered or already failed. A duplicate run must not
            # send a customer the same message twice.
            return message.status

        conversation = (
            (
                await session.execute(
                    select(Conversation).where(
                        Conversation.id == message.conversation_id
                    )
                )
            )
            .unique()
            .scalar_one()
        )

        outbound = OutboundMessage(
            to_address=message.to_address,
            to_name=conversation.display_name,
            subject=message.subject,
            body_text=message.body_text,
            body_html=message.body_html,
            in_reply_to=message.in_reply_to,
        )
        channel_name = conversation.channel

    # Outside the transaction: a provider round trip has no business holding a
    # database connection open for its duration.
    try:
        channel = build_channel(channel_name)
        result = await channel.send(outbound)
    except MessagingError as exc:
        async with tenant_scope(organization) as session:
            failed = (
                (
                    await session.execute(
                        select(Message).where(Message.id == UUID(message_id))
                    )
                )
                .unique()
                .scalar_one()
            )
            if exc.retryable:
                # Left `queued`, so a retry picks it up unchanged and the
                # sender sees it as still in flight rather than as failed.
                logger.warning(
                    "message_delivery_retrying", extra={"message_id": message_id}
                )
                raise
            failed.status = "failed"
            failed.failure_reason = str(exc)[:500]
        logger.warning(
            "message_delivery_rejected",
            extra={"message_id": message_id, "reason": str(exc)},
        )
        return "failed"

    async with tenant_scope(organization) as session:
        sent = (
            (
                await session.execute(
                    select(Message).where(Message.id == UUID(message_id))
                )
            )
            .unique()
            .scalar_one()
        )
        sent.status = "sent"
        sent.sent_at = datetime.now(UTC)
        sent.provider_message_id = result.provider_message_id
        sent.rfc_message_id = result.rfc_message_id

    logger.info(
        "message_delivered",
        extra={"message_id": message_id, "channel": channel_name},
    )
    return "sent"
