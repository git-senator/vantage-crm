"""Notification jobs — where email delivery finally happens.

Phase 2.7 left a seam in `TaskService._notify_assignment`: it recorded that a
notification *should* be sent and logged it, because an inline SMTP call inside
a request transaction makes task assignment as slow and as failure-prone as the
mail provider. This is the other side of that seam.

The transactional-outbox purist would say the enqueue should be part of the
caller's transaction. It is not, and the reason is proportionality: the failure
mode is one missed "you were assigned a task" email, which is recoverable by a
human looking at their task list. Anything whose loss would corrupt data does
not go through the queue at all — see `app/workers/queue.py`.

Delivery goes through `NotificationService`, which is transport-agnostic; this
module knows nothing about SES.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.repositories.task import TaskRepository
from app.repositories.user import UserRepository
from app.services.notifications.base import EmailAddress, NotificationError
from app.services.notifications.service import NotificationService
from app.workers.context import tenant_scope
from app.workers.runner import job

logger = get_logger(__name__)


@job(organization_arg=2)
async def notify_task_assigned(
    ctx: dict[str, Any], task_id: str, assignee_id: str, organization_id: str
) -> str:
    """Tell someone a task landed on them.

    Reads the task and the assignee at *send* time rather than trusting what
    was enqueued: between assignment and delivery the task may have been
    reassigned, completed, or deleted, and mailing someone about work that is
    no longer theirs is worse than not mailing them at all.
    """
    organization = UUID(organization_id)

    async with tenant_scope(organization) as session:
        task = await TaskRepository(session).get(UUID(task_id), organization)
        if task is None or task.deleted_at is not None:
            return "task_gone"
        if str(task.assignee_id) != assignee_id:
            return "reassigned"
        if task.completed_at is not None:
            return "already_done"

        assignee = await UserRepository(session).get(UUID(assignee_id), organization)
        if assignee is None or not assignee.is_active:
            return "assignee_unavailable"

        recipient = EmailAddress(address=assignee.email, name=assignee.full_name)
        due = task.due_at.strftime("%d %b %Y") if task.due_at else None

    # Outside the transaction: a mail provider round trip has no business
    # holding a database connection open, and nothing after this point writes.
    try:
        await NotificationService().send_task_assigned(
            to=recipient, task_title=task.title, due_on=due
        )
    except NotificationError as exc:
        if not exc.retryable:
            # A rejected address will be rejected again. Dead-letter it now
            # rather than spending five attempts proving that.
            logger.warning(
                "task_assignment_email_rejected", extra={"task_id": task_id}
            )
            return "rejected"
        raise

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
