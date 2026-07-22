"""The notification centre.

The two properties worth defending here are the ones a notification system
usually gets wrong.

**A notification belongs to its recipient and to nobody else** — not to their
manager, not to an admin at ALL scope. `TestVisibility` proves the scope
resolver cannot widen it, because "who was told what" is somebody's inbox, not
an administrative view of the CRM.

**Muting suppresses the bell, not the history.** `TestPreferences` pins that
down: a muted notification is still written, pre-read, so a user who later asks
"when was I told about this?" gets an answer.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationPreference
from app.schemas.notification import NotificationPreferenceUpdate
from app.schemas.task import TaskCreate
from app.services.notification_center import (
    DEFAULT_PREFERENCES,
    NotificationCenter,
)
from app.services.task import TaskService
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


async def _raise(center, organization, recipient, **overrides):  # type: ignore[no-untyped-def]
    payload = {
        "organization_id": organization.id,
        "recipient_id": recipient.id,
        "category": "deal",
        "type": "deal.stage_changed",
        "title": "A deal moved",
        "body": "88 Townsend went to Under Contract.",
        **overrides,
    }
    return await center.raise_notification(**payload)


class TestRaising:
    async def test_a_notification_lands_unread(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        _user, _auth = admin
        recipient = await make_user(db, organization, "agent@vantage.example")
        center = NotificationCenter(db)

        notification = await _raise(center, organization, recipient)

        assert notification is not None
        assert notification.read_at is None
        assert await center.unread_count(organization.id, recipient.id) == 1

    async def test_nobody_is_told_about_their_own_action(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A product that notifies you about what you just did is one people
        learn to ignore."""
        actor = await make_user(db, organization, "self@vantage.example")
        center = NotificationCenter(db)

        result = await _raise(center, organization, actor, actor_id=actor.id)

        assert result is None
        assert await center.unread_count(organization.id, actor.id) == 0

    async def test_a_machine_notification_has_no_actor(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """What separates "the scanner quarantined your upload" from "Sofia
        mentioned you"."""
        recipient = await make_user(db, organization, "agent@vantage.example")
        notification = await _raise(
            NotificationCenter(db),
            organization,
            recipient,
            category="system",
            type="document.quarantined",
        )
        assert notification is not None
        assert notification.actor_id is None


class TestPreferences:
    async def test_defaults_cover_every_category_without_writing_rows(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A default is not a choice. Writing preference rows for people who
        never opened the screen would make a later change to a default
        invisible to them."""
        user = await make_user(db, organization, "agent@vantage.example")
        resolved = await NotificationCenter(db).get_preferences(
            organization.id, user.id
        )

        assert {row.category for row in resolved} == set(DEFAULT_PREFERENCES)
        stored = (
            (await db.execute(select(NotificationPreference))).scalars().all()
        )
        assert stored == []

    async def test_muting_suppresses_the_bell_not_the_history(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "agent@vantage.example")
        center = NotificationCenter(db)
        await center.update_preferences(
            organization.id,
            user.id,
            [
                NotificationPreferenceUpdate(
                    category="deal", in_app=False, email=False
                )
            ],
        )

        notification = await _raise(center, organization, user)

        # Written, so the history survives …
        assert notification is not None
        # … but pre-read, so the bell stays quiet.
        assert notification.read_at is not None
        assert await center.unread_count(organization.id, user.id) == 0

    async def test_an_email_preference_queues_delivery(
        self, db: AsyncSession, organization, admin, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        queued: list[tuple] = []

        async def _capture(job_name: str, *args: object, **kwargs: object) -> str:
            queued.append((job_name, args))
            return "job-id"

        monkeypatch.setattr(
            "app.services.notification_center.enqueue", _capture
        )

        user = await make_user(db, organization, "agent@vantage.example")
        center = NotificationCenter(db)
        await center.update_preferences(
            organization.id,
            user.id,
            [NotificationPreferenceUpdate(category="deal", in_app=True, email=True)],
        )

        await _raise(center, organization, user)

        assert len(queued) == 1
        assert queued[0][0] == "deliver_notification_email"

    async def test_no_email_preference_queues_nothing(
        self, db: AsyncSession, organization, admin, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        queued: list[tuple] = []

        async def _capture(job_name: str, *args: object, **kwargs: object) -> str:
            queued.append((job_name, args))
            return "job-id"

        monkeypatch.setattr(
            "app.services.notification_center.enqueue", _capture
        )
        user = await make_user(db, organization, "agent@vantage.example")

        # `deal` defaults to in-app only — email on by default everywhere is how
        # a CRM ends up in a spam filter, taking its password resets with it.
        await _raise(NotificationCenter(db), organization, user)

        assert queued == []

    async def test_updating_preferences_is_upsert(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "agent@vantage.example")
        center = NotificationCenter(db)

        await center.update_preferences(
            organization.id,
            user.id,
            [NotificationPreferenceUpdate(category="task", in_app=True, email=False)],
        )
        resolved = await center.update_preferences(
            organization.id,
            user.id,
            [NotificationPreferenceUpdate(category="task", in_app=False, email=True)],
        )

        task_pref = next(row for row in resolved if row.category == "task")
        assert (task_pref.in_app, task_pref.email) == (False, True)
        rows = (
            (
                await db.execute(
                    select(NotificationPreference).where(
                        NotificationPreference.category == "task"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


class TestVisibility:
    async def test_one_users_notifications_are_invisible_to_another(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Including to an admin. Widening a scope must not widen this."""
        admin_user, _auth = admin
        recipient = await make_user(db, organization, "agent@vantage.example")
        center = NotificationCenter(db)
        await _raise(center, organization, recipient)

        mine, _more, unread = await center.list_for_user(
            organization.id, admin_user.id
        )
        assert mine == []
        assert unread == 0

    async def test_marking_another_users_notification_read_is_a_miss(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        admin_user, _auth = admin
        recipient = await make_user(db, organization, "agent@vantage.example")
        center = NotificationCenter(db)
        notification = await _raise(center, organization, recipient)
        assert notification is not None

        result = await center.mark_read(
            notification.id, organization.id, admin_user.id
        )
        assert result is None
        assert notification.read_at is None


class TestMarkingRead:
    async def test_mark_read_is_idempotent(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """"When did you first see this" is the useful fact, so a second read
        must not move the timestamp."""
        user = await make_user(db, organization, "agent@vantage.example")
        center = NotificationCenter(db)
        notification = await _raise(center, organization, user)
        assert notification is not None

        first = await center.mark_read(notification.id, organization.id, user.id)
        assert first is not None and first.read_at is not None
        stamp = first.read_at

        second = await center.mark_read(notification.id, organization.id, user.id)
        assert second is not None and second.read_at == stamp

    async def test_mark_all_read_clears_only_this_users_unread(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        center = NotificationCenter(db)
        mine = await make_user(db, organization, "mine@vantage.example")
        theirs = await make_user(db, organization, "theirs@vantage.example")
        await _raise(center, organization, mine)
        await _raise(center, organization, mine)
        await _raise(center, organization, theirs)

        cleared = await center.mark_all_read(organization.id, mine.id)

        assert cleared == 2
        assert await center.unread_count(organization.id, mine.id) == 0
        assert await center.unread_count(organization.id, theirs.id) == 1


class TestTaskAssignmentRaisesOne:
    async def test_assigning_a_task_notifies_the_assignee(
        self, db: AsyncSession, organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """The end of the seam Phase 2.7 opened: a service says what happened,
        and the centre decides how it is delivered."""
        manager = await make_user(db, organization, "mgr@vantage.example")
        member = await make_user(db, organization, "mem@vantage.example")
        manager_auth = await auth_for(db, manager, "admin")

        await TaskService(db, manager_auth).create_task(
            TaskCreate(title="Send the disclosure packet", assignee_id=member.id),
            manager,
        )

        rows = (
            (
                await db.execute(
                    select(Notification).where(Notification.user_id == member.id)
                )
            )
            .unique()
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].type == "task.assigned"
        assert rows[0].actor_id == manager.id
        assert rows[0].entity_type == "task"

    async def test_self_assignment_notifies_nobody(
        self, db: AsyncSession, organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        actor = await make_user(db, organization, "solo@vantage.example")
        auth = await auth_for(db, actor, "admin")

        await TaskService(db, auth).create_task(
            TaskCreate(title="My own task"), actor
        )

        rows = (await db.execute(select(Notification))).unique().scalars().all()
        assert rows == []
