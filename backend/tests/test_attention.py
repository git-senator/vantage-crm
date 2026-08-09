"""The sidebar's attention counts.

Two things are worth pinning down, and neither is the arithmetic.

**Each count is personal.** A colleague's thread and a colleague's task must not
light my dot, or the sidebar stops meaning "waiting on you" and becomes "the
company is busy" — which nobody can act on.

**A missing permission returns zero, not the truth.** The counts travel to a
sidebar that renders for everyone, so a number a caller is not allowed to know
must not be in the payload at all. `TestPermissionsGateEachCount` is the reason
this file exists.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.access_request import AccessRequest
from app.models.conversation import Conversation
from app.models.notification import Notification
from app.models.task import Task
from app.services.attention import AttentionService

pytestmark = pytest.mark.integration


async def _thread(
    db: AsyncSession, *, organization_id, owner_id, unread: int
) -> Conversation:
    thread = Conversation(
        organization_id=organization_id,
        owner_id=owner_id,
        channel="whatsapp",
        external_id=f"wa-{owner_id}-{unread}-{datetime.now(UTC).timestamp()}",
        unread_count=unread,
    )
    db.add(thread)
    await db.flush()
    return thread


async def _task(
    db: AsyncSession, *, organization_id, assignee_id, due_in: timedelta | None, status="todo"
) -> Task:
    row = Task(
        organization_id=organization_id,
        assignee_id=assignee_id,
        title="Call the buyer back",
        status=status,
        due_at=None if due_in is None else datetime.now(UTC) + due_in,
        # The table refuses a `done` task with no completion time, and rightly:
        # a status that disagrees with the timestamp is unanswerable.
        completed_at=datetime.now(UTC) if status == "done" else None,
    )
    db.add(row)
    await db.flush()
    return row


async def _notification(
    db: AsyncSession, *, organization_id, user_id, read: bool
) -> Notification:
    row = Notification(
        organization_id=organization_id,
        user_id=user_id,
        category="task",
        type="task_assigned",
        title="A task was assigned to you",
        read_at=datetime.now(UTC) if read else None,
    )
    db.add(row)
    await db.flush()
    return row


class TestCountsArePersonal:
    async def test_only_my_threads_count(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        mine, mine_auth = agent
        theirs, _ = other_agent
        await _thread(
            db, organization_id=mine.organization_id, owner_id=mine.id, unread=2
        )
        await _thread(
            db, organization_id=mine.organization_id, owner_id=theirs.id, unread=9
        )

        summary = await AttentionService(db, mine_auth).summary()
        assert summary.messages == 1

    async def test_a_read_thread_does_not_count(
        self, db: AsyncSession, agent
    ) -> None:  # type: ignore[no-untyped-def]
        mine, mine_auth = agent
        await _thread(
            db, organization_id=mine.organization_id, owner_id=mine.id, unread=0
        )

        summary = await AttentionService(db, mine_auth).summary()
        assert summary.messages == 0

    async def test_only_my_unread_notifications_count(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        mine, mine_auth = agent
        theirs, _ = other_agent
        await _notification(
            db, organization_id=mine.organization_id, user_id=mine.id, read=False
        )
        await _notification(
            db, organization_id=mine.organization_id, user_id=mine.id, read=True
        )
        await _notification(
            db, organization_id=mine.organization_id, user_id=theirs.id, read=False
        )

        summary = await AttentionService(db, mine_auth).summary()
        assert summary.notifications == 1


class TestTasksUseTheDeadline:
    async def test_a_task_due_soon_counts(self, db: AsyncSession, agent) -> None:  # type: ignore[no-untyped-def]
        mine, mine_auth = agent
        await _task(
            db,
            organization_id=mine.organization_id,
            assignee_id=mine.id,
            due_in=timedelta(hours=2),
        )
        assert (await AttentionService(db, mine_auth).summary()).tasks == 1

    async def test_an_overdue_task_counts(self, db: AsyncSession, agent) -> None:  # type: ignore[no-untyped-def]
        mine, mine_auth = agent
        await _task(
            db,
            organization_id=mine.organization_id,
            assignee_id=mine.id,
            due_in=timedelta(days=-3),
        )
        assert (await AttentionService(db, mine_auth).summary()).tasks == 1

    async def test_a_task_due_next_week_does_not(
        self, db: AsyncSession, agent
    ) -> None:  # type: ignore[no-untyped-def]
        """The dot means "waiting on you", not "exists". A deadline a week out
        is not waiting on anybody today, and a dot that is always lit is a dot
        nobody reads."""
        mine, mine_auth = agent
        await _task(
            db,
            organization_id=mine.organization_id,
            assignee_id=mine.id,
            due_in=timedelta(days=7),
        )
        assert (await AttentionService(db, mine_auth).summary()).tasks == 0

    async def test_a_finished_task_does_not(self, db: AsyncSession, agent) -> None:  # type: ignore[no-untyped-def]
        mine, mine_auth = agent
        await _task(
            db,
            organization_id=mine.organization_id,
            assignee_id=mine.id,
            due_in=timedelta(hours=1),
            status="done",
        )
        assert (await AttentionService(db, mine_auth).summary()).tasks == 0

    async def test_a_task_with_no_deadline_does_not(
        self, db: AsyncSession, agent
    ) -> None:  # type: ignore[no-untyped-def]
        mine, mine_auth = agent
        await _task(
            db,
            organization_id=mine.organization_id,
            assignee_id=mine.id,
            due_in=None,
        )
        assert (await AttentionService(db, mine_auth).summary()).tasks == 0


class TestPermissionsGateEachCount:
    async def test_pending_requests_are_invisible_without_users_manage(
        self, db: AsyncSession, agent
    ) -> None:  # type: ignore[no-untyped-def]
        """An agent cannot open the review queue, so the sidebar must not tell
        them how long it is."""
        _mine, mine_auth = agent
        db.add(AccessRequest(email="hopeful@example.com", full_name="Hope", status="pending"))
        await db.flush()

        assert (await AttentionService(db, mine_auth).summary()).requests == 0

    async def test_an_admin_sees_the_pending_queue(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        _account, auth = admin
        db.add(AccessRequest(email="hopeful@example.com", full_name="Hope", status="pending"))
        db.add(
            AccessRequest(email="done@example.com", full_name="Dee", status="approved")
        )
        await db.flush()

        assert (await AttentionService(db, auth).summary()).requests == 1


class TestQuietByDefault:
    async def test_a_clean_workspace_lights_nothing(
        self, db: AsyncSession, agent
    ) -> None:  # type: ignore[no-untyped-def]
        """The dark state is the one people see most, and it has to be right:
        a dot that is lit on an empty workspace teaches everyone to ignore it."""
        _mine, mine_auth = agent
        summary = await AttentionService(db, mine_auth).summary()

        assert (summary.messages, summary.tasks, summary.requests, summary.notifications) == (
            0,
            0,
            0,
            0,
        )
