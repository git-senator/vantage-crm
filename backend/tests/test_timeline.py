"""Unified timeline — activities and notes merged on one chronology.

The timeline is a composite read, and what these tests pin down is the merge and
its authorization:

  * two sources, **activities + notes**, interleaved by timestamp — a note by
    `created_at`, an activity by `occurred_at` (when it happened, not when it was
    logged);
  * the per-entity timeline is gated on reading the parent, once;
  * the feed shows only the sources the caller may view, within scope — a caller
    with `activities.view` but not `notes.view` sees activities alone, no 403;
  * system-written activities (stage_change) carry `is_system`, pinned notes
    carry `is_pinned`, through to the merged item.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.permissions import Scope
from app.models.organization import Organization
from app.schemas.activity import ActivityCreate
from app.schemas.lead import LeadCreate
from app.schemas.note import NoteCreate
from app.services.activity import ActivityService
from app.services.lead import LeadService
from app.services.note import NoteService
from app.services.rbac import AuthorizationContext
from app.services.timeline import TimelineService
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


@pytest.fixture
async def lead_record(db: AsyncSession, admin):  # type: ignore[no-untyped-def]
    user, auth = admin
    return await LeadService(db, auth).create_lead(
        LeadCreate(first_name="Imani", last_name="Bello"), user
    )


class TestEntityTimeline:
    async def test_merges_activities_and_notes_newest_first(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        # An activity that HAPPENED yesterday (older on the timeline)...
        await ActivityService(db, auth).create_activity(
            ActivityCreate(
                entity_type="lead",
                entity_id=lead_record.id,
                type="call",
                subject="Intro call",
                occurred_at=datetime.now(UTC) - timedelta(days=1),
            ),
            user,
        )
        # ...and a note written just now (newer).
        await NoteService(db, auth).create_note(
            NoteCreate(entity_type="lead", entity_id=lead_record.id, body="Fresh note"),
            user,
        )

        items = await TimelineService(db, auth).for_entity(
            entity_type="lead", entity_id=lead_record.id
        )
        assert [i.kind for i in items] == ["note", "activity"]
        assert items[0].body == "Fresh note"
        assert items[1].title == "Intro call"

    async def test_gated_on_reading_the_parent(
        self, db: AsyncSession, admin, organization: Organization, rbac_seeded, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await NoteService(db, auth).create_note(
            NoteCreate(entity_type="lead", entity_id=lead_record.id, body="private"),
            user,
        )
        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        with pytest.raises(NotFoundError):
            await TimelineService(db, agent_auth).for_entity(
                entity_type="lead", entity_id=lead_record.id
            )

    async def test_pinned_note_carries_through(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await NoteService(db, auth).create_note(
            NoteCreate(
                entity_type="lead",
                entity_id=lead_record.id,
                body="Read me first",
                is_pinned=True,
            ),
            user,
        )
        items = await TimelineService(db, auth).for_entity(
            entity_type="lead", entity_id=lead_record.id
        )
        note_items = [i for i in items if i.kind == "note"]
        assert note_items[0].is_pinned is True

    async def test_system_activity_is_marked(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        """A stage_change is system-written; the merged item says so, and the UI
        uses that to lock editing."""
        user, auth = admin
        # Write a system activity directly (the deal transition's path).
        await ActivityService(db).record(
            organization_id=auth.organization_id,
            actor_id=user.id,
            entity_type="lead",
            entity_id=lead_record.id,
            type="stage_change",
            subject="Moved to Qualified",
        )
        items = await TimelineService(db, auth).for_entity(
            entity_type="lead", entity_id=lead_record.id
        )
        system = [i for i in items if i.type == "stage_change"]
        assert len(system) == 1
        assert system[0].is_system is True


class TestFeed:
    async def test_feed_merges_within_scope(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await ActivityService(db, auth).create_activity(
            ActivityCreate(
                entity_type="lead",
                entity_id=lead_record.id,
                type="email",
                subject="Sent brochure",
            ),
            user,
        )
        await NoteService(db, auth).create_note(
            NoteCreate(entity_type="lead", entity_id=lead_record.id, body="A note"),
            user,
        )
        items = await TimelineService(db, auth).feed()
        kinds = {i.kind for i in items}
        assert kinds == {"activity", "note"}

    async def test_feed_omits_a_source_the_caller_cannot_view(
        self, db: AsyncSession, admin, organization: Organization, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        """activities.view but no notes.view: the note is invisible in the feed,
        and the call does not 403."""
        user, admin_auth = admin
        await ActivityService(db, admin_auth).create_activity(
            ActivityCreate(
                entity_type="lead",
                entity_id=lead_record.id,
                type="call",
                subject="Logged call",
            ),
            user,
        )
        await NoteService(db, admin_auth).create_note(
            NoteCreate(entity_type="lead", entity_id=lead_record.id, body="Hidden"),
            user,
        )

        # A context that can view activities (ALL) but holds no notes grant.
        partial = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("custom",),
            grants={"activities.view": Scope.ALL},
        )
        items = await TimelineService(db, partial).feed()
        assert all(i.kind == "activity" for i in items)
        assert any(i.title == "Logged call" for i in items)
