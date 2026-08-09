"""What is waiting on the signed-in person, in four numbers.

Backs the dots in the sidebar. The question each one answers is deliberately
**"is something waiting on you"**, not "has something arrived since you last
looked" — the second needs a per-user, per-section last-seen timestamp, and it
goes dark the moment someone glances at a page, including at the thread they did
not answer. This one is derived entirely from state the database already holds,
so there is nothing to mark as seen and nothing that can drift out of sync.

Every count is personal. Ownership is what makes it so: a conversation belongs
to an owner, a task to an assignee, a notification to a user. The one exception
is access requests, which belong to whoever administers the workspace rather
than to a person — so that count is gated on the permission instead.

**Permissions gate each count independently, and a caller without one gets
zero rather than the truth.** Otherwise the sidebar becomes a side channel: an
agent who cannot open the access-request queue would still learn how many
requests are sitting in it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.access_request import AccessRequest
from app.models.conversation import Conversation
from app.models.notification import Notification
from app.models.task import Task
from app.schemas.attention import AttentionRead
from app.services.rbac import AuthorizationContext

#: How far ahead a deadline counts as "waiting on you".
#:
#: A rolling window rather than "today", because the workspace timezone is
#: stored as a display string ("Mountain Time") and cannot be turned into a
#: reliable calendar-day boundary. Twelve hours needs no timezone, reads the
#: same in every country, and does not light up for a task due the day after
#: tomorrow.
DUE_SOON = timedelta(hours=12)


class AttentionService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth

    async def summary(self) -> AttentionRead:
        return AttentionRead(
            messages=await self._messages(),
            tasks=await self._tasks(),
            requests=await self._requests(),
            notifications=await self._notifications(),
        )

    def _may(self, permission: str) -> bool:
        """Whether the caller holds a permission at all.

        `scope_for` rather than `require`: a missing permission here is not an
        error, it is a zero.
        """
        return self.auth.scope_for(permission) is not None

    async def _count(self, query) -> int:  # type: ignore[no-untyped-def]
        return int((await self.session.execute(query)).scalar_one() or 0)

    async def _messages(self) -> int:
        """Threads assigned to me with something unread in them.

        `unread_count` is shared across the team by design — one manager opens
        the thread and it clears for everyone — so the count alone cannot say
        whether a thread is waiting on *me*. Ownership can.
        """
        if self.auth.user_id is None or not self._may("contacts.view"):
            return 0
        return await self._count(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.organization_id == self.auth.organization_id)
            .where(Conversation.owner_id == self.auth.user_id)
            .where(Conversation.unread_count > 0)
            .where(Conversation.deleted_at.is_(None))
        )

    async def _tasks(self) -> int:
        """My unfinished tasks that are overdue or due within `DUE_SOON`."""
        if self.auth.user_id is None or not self._may("tasks.view"):
            return 0
        return await self._count(
            select(func.count())
            .select_from(Task)
            .where(Task.organization_id == self.auth.organization_id)
            .where(Task.assignee_id == self.auth.user_id)
            .where(Task.status != "done")
            .where(Task.deleted_at.is_(None))
            .where(Task.due_at.is_not(None))
            .where(Task.due_at <= datetime.now(UTC) + DUE_SOON)
        )

    async def _requests(self) -> int:
        """Access requests nobody has answered yet.

        Not personal — a request belongs to whoever administers the workspace —
        so the permission is what scopes it, and `users.manage` is the same gate
        the review queue itself sits behind.

        Deliberately **not** filtered by organization: a request arrives from
        someone who has no account yet, so `organization_id` is null until the
        moment it is approved (see `AccessRequest`). Filtering on it here would
        count every pending request as zero — the one state this is meant to
        find.
        """
        if not self._may("users.manage"):
            return 0
        return await self._count(
            select(func.count())
            .select_from(AccessRequest)
            .where(AccessRequest.status == "pending")
        )

    async def _notifications(self) -> int:
        """My unread notifications. No permission gates a person's own bell."""
        if self.auth.user_id is None:
            return 0
        return await self._count(
            select(func.count())
            .select_from(Notification)
            .where(Notification.organization_id == self.auth.organization_id)
            .where(Notification.user_id == self.auth.user_id)
            .where(Notification.read_at.is_(None))
        )
