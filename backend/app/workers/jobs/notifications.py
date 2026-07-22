"""Notification jobs — where email delivery finally happens.

Phase 3.2 wired task assignment straight to an email job. Phase 3.3 replaced
that with the notification centre: a service raises a notification, the centre
decides — from the recipient's own preferences — whether it also becomes an
email, and this job delivers it. One delivery path for every notification type
beats one job per event, which is how a notification system ends up with six
subtly different ideas of what "already sent" means.

The transactional-outbox purist would say the enqueue should be part of the
caller's transaction. It is not, and the reason is proportionality: the failure
mode is one missed "you were assigned a task" email, which is recoverable by a
human looking at their task list. Anything whose loss would corrupt data does
not go through the queue at all — see `app/workers/queue.py`.

Delivery goes through `NotificationService`, which is transport-agnostic; this
module knows nothing about SES.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update

from app.core.logging import get_logger
from app.models.notification import Notification
from app.repositories.user import UserRepository
from app.services.notifications.base import EmailAddress, NotificationError
from app.services.notifications.service import NotificationService
from app.workers.context import tenant_scope
from app.workers.runner import job

logger = get_logger(__name__)


@job(organization_arg=1)
async def deliver_notification_email(
    ctx: dict[str, Any], notification_id: str, organization_id: str
) -> str:
    """Email one notification to its recipient.

    The in-app row is the source of truth and already exists — this job only
    delivers a copy. It re-reads the row at send time rather than trusting the
    enqueue: a notification the user has already read in the app does not need
    to arrive in their inbox thirty seconds later, and `emailed_at` makes a
    duplicate delivery impossible even if the job runs twice.
    """
    organization = UUID(organization_id)

    async with tenant_scope(organization) as session:
        notification = (
            await session.execute(
                select(Notification)
                .where(Notification.id == UUID(notification_id))
                .where(Notification.organization_id == organization)
            )
        ).unique().scalar_one_or_none()

        if notification is None:
            return "gone"
        if notification.emailed_at is not None:
            return "already_sent"
        if notification.read_at is not None:
            # Seen in the app before the queue got to it. Sending anyway is how
            # a product teaches people that its emails are not worth opening.
            return "already_read"

        recipient = await UserRepository(session).get(
            notification.user_id, organization
        )
        if recipient is None or not recipient.is_active:
            return "recipient_unavailable"

        to = EmailAddress(address=recipient.email, name=recipient.full_name)
        subject = notification.title
        body = notification.body or ""

    try:
        await NotificationService().send_notification(
            to=to, subject=subject, body=body, category=notification.category
        )
    except NotificationError as exc:
        if not exc.retryable:
            logger.warning(
                "notification_email_rejected",
                extra={"notification_id": notification_id},
            )
            return "rejected"
        raise

    # Stamped in a second transaction, deliberately: the first one is closed
    # before a mail provider round trip, and a connection held open across a
    # network call to a third party is a connection lost to its timeout.
    async with tenant_scope(organization) as session:
        await session.execute(
            update(Notification)
            .where(Notification.id == UUID(notification_id))
            .values(emailed_at=datetime.now(UTC))
        )

    return "sent"


@job()
async def send_email(
    ctx: dict[str, Any],
    *,
    to_address: str,
    to_name: str | None,
    subject: str,
    html_body: str,
    text_body: str,
    category: str = "general",
) -> str:
    """Generic queued delivery, for callers with their own copy.

    Templated intents belong on `NotificationService` so that switching
    provider cannot change what a customer receives. This exists for one-off
    sends and for jobs that compose their own message; it is not the path a
    product feature should reach for first.
    """
    result = await NotificationService().send_raw(
        to=EmailAddress(address=to_address, name=to_name),
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        category=category,
    )
    return result.provider_message_id
