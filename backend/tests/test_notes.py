"""Notes — rich-text annotations pinned to a record.

A note is a document, not a timeline event, which is why it is its own table.
What this file proves is the same authorization shape activities have, plus the
things specific to notes:

  * a note has **no scope anchor of its own** — visibility follows the parent
    record through `EntityAccess`, editing follows authorship;
  * a note can annotate a **task**, which an activity cannot — the vocabulary
    is a superset, and `EntityAccess` gained a task case for it;
  * **pinned notes lead** a record's list — the "read me first" context;
  * `content_format` carries rich text (markdown by default), stored verbatim.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.audit import AuditLog
from app.models.note import Note
from app.models.organization import Organization
from app.schemas.lead import LeadCreate
from app.schemas.note import NoteCreate, NoteFilters, NoteUpdate
from app.schemas.task import TaskCreate
from app.services.lead import LeadService
from app.services.note import NoteService
from app.services.rbac import AuthorizationContext
from app.services.task import TaskService
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


@pytest.fixture
async def lead_record(db: AsyncSession, admin):  # type: ignore[no-untyped-def]
    user, auth = admin
    return await LeadService(db, auth).create_lead(
        LeadCreate(first_name="Rowan", last_name="Ellis"), user
    )


def _create(entity_id, **overrides: object) -> NoteCreate:  # type: ignore[no-untyped-def]
    data: dict = {
        "entity_type": "lead",
        "entity_id": entity_id,
        "body": "Buyer is pre-approved to 1.8M; wants a quick close.",
        **overrides,
    }
    return NoteCreate(**data)


# ------------------------------------------------------------------- writing


class TestWriting:
    async def test_create_writes_and_audits(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        note = await NoteService(db, auth).create_note(_create(lead_record.id), user)
        assert note.author_id == user.id
        assert note.content_format == "markdown"

        entries = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_CREATED)
            )
        ).scalars().all()
        assert any(e.entity_type == "note" for e in entries)

    async def test_rich_text_format_is_stored(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        note = await NoteService(db, auth).create_note(
            _create(lead_record.id, body="# Heading\n\n- one\n- two", content_format="markdown"),
            user,
        )
        assert note.content_format == "markdown"
        assert "# Heading" in note.body

    async def test_cannot_annotate_a_record_you_cannot_see(
        self, db: AsyncSession, organization: Organization, rbac_seeded, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        with pytest.raises(NotFoundError):
            await NoteService(db, agent_auth).create_note(_create(lead_record.id), agent)

    async def test_requires_the_manage_permission(
        self, db: AsyncSession, organization: Organization, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        viewer = await make_user(db, organization, "viewer@vantage.example")
        viewer_auth = AuthorizationContext(
            user_id=viewer.id,
            organization_id=organization.id,
            role_keys=("viewer",),
            grants={"leads.view": Scope.ALL, "notes.view": Scope.ALL},
        )
        with pytest.raises(PermissionDeniedError):
            await NoteService(db, viewer_auth).create_note(_create(lead_record.id), viewer)

    async def test_a_note_can_annotate_a_task(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The vocabulary is a superset of activities' — a note may hang off a
        task, and EntityAccess resolves it through the task's own scope."""
        user, auth = admin
        task = await TaskService(db, auth).create_task(
            TaskCreate(title="Chase the appraisal"), user
        )
        note = await NoteService(db, auth).create_note(
            _create(task.id, entity_type="task", body="Appraiser booked for Tue."),
            user,
        )
        assert note.entity_type == "task"
        assert note.entity_id == task.id


# ------------------------------------------------------ read authorization


class TestReadAuthorization:
    async def test_per_entity_read_follows_the_parent(
        self, db: AsyncSession, admin, organization: Organization, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await NoteService(db, auth).create_note(_create(lead_record.id), user)

        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        with pytest.raises(NotFoundError):
            await NoteService(db, agent_auth).list_for_entity(
                entity_type="lead", entity_id=lead_record.id
            )

    async def test_visible_to_whoever_can_read_the_record(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await NoteService(db, auth).create_note(_create(lead_record.id), user)
        rows = await NoteService(db, auth).list_for_entity(
            entity_type="lead", entity_id=lead_record.id
        )
        assert len(rows) == 1

    async def test_feed_is_author_scoped(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        one = await make_user(db, organization, "one@vantage.example")
        two = await make_user(db, organization, "two@vantage.example")
        one_auth = await auth_for(db, one, "agent")
        two_auth = await auth_for(db, two, "agent")

        lead_one = await LeadService(db, one_auth).create_lead(
            LeadCreate(first_name="A", last_name="One"), one
        )
        lead_two = await LeadService(db, two_auth).create_lead(
            LeadCreate(first_name="B", last_name="Two"), two
        )
        await NoteService(db, one_auth).create_note(
            _create(lead_one.id, title="One's note"), one
        )
        await NoteService(db, two_auth).create_note(
            _create(lead_two.id, title="Two's note"), two
        )

        rows, _ = await NoteService(db, one_auth).list_feed(
            filters=NoteFilters(), limit=50
        )
        assert [r.title for r in rows] == ["One's note"]


# ----------------------------------------------------------------- pinning


class TestPinning:
    async def test_pinned_notes_lead_the_list(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = NoteService(db, auth)
        await service.create_note(_create(lead_record.id, title="ordinary"), user)
        await service.create_note(
            _create(lead_record.id, title="pinned", is_pinned=True), user
        )

        rows = await service.list_for_entity(
            entity_type="lead", entity_id=lead_record.id
        )
        assert rows[0].title == "pinned"

    async def test_pinning_via_update(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = NoteService(db, auth)
        note = await service.create_note(_create(lead_record.id), user)
        updated = await service.update_note(note.id, NoteUpdate(is_pinned=True), user)
        assert updated.is_pinned is True


# ------------------------------------------------------------- corrections


class TestCorrections:
    async def test_edit_your_own_note(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = NoteService(db, auth)
        note = await service.create_note(_create(lead_record.id), user)
        updated = await service.update_note(
            note.id, NoteUpdate(body="Revised: pre-approved to 2.0M."), user
        )
        assert "2.0M" in updated.body

    async def test_agent_cannot_edit_someone_elses_note(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """A shared property both agents can read; only the author may edit."""
        from app.schemas.property import PropertyCreate
        from app.services.property import PropertyService

        one = await make_user(db, organization, "one@vantage.example")
        two = await make_user(db, organization, "two@vantage.example")
        one_auth = await auth_for(db, one, "agent")
        two_auth = await auth_for(db, two, "agent")

        listing = await PropertyService(db, one_auth).create_property(
            PropertyCreate(
                title="9 Oak",
                address_line1="9 Oak Ave",
                city="Denver",
                state="CO",
                postal_code="80202",
                property_type="single_family",
            ),
            one,
        )
        note = await NoteService(db, one_auth).create_note(
            _create(listing.id, entity_type="property", body="Seller motivated."), one
        )

        with pytest.raises(PermissionDeniedError):
            await NoteService(db, two_auth).update_note(
                note.id, NoteUpdate(body="hijacked"), two
            )

    async def test_delete_is_soft_and_audits(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = NoteService(db, auth)
        note = await service.create_note(_create(lead_record.id), user)
        await service.delete_note(note.id, user)

        with pytest.raises(NotFoundError):
            await service.get_note(note.id)

        row = (
            await db.execute(select(Note).where(Note.id == note.id))
        ).unique().scalar_one()
        assert row.deleted_at is not None

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_DELETED)
            )
        ).scalar_one()
        assert entry.entity_type == "note"


# --------------------------------------------------------------- search


class TestSearch:
    async def test_search_matches_title_and_body(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = NoteService(db, auth)
        await service.create_note(
            _create(lead_record.id, body="Discussed bridge financing at length"), user
        )
        await service.create_note(
            _create(lead_record.id, body="Left a voicemail about the inspection"), user
        )
        rows, _ = await service.list_feed(
            filters=NoteFilters(search="financing"), limit=50
        )
        assert len(rows) == 1
        assert "financing" in rows[0].body


# --------------------------------------------------------- tenant isolation


class TestTenantIsolation:
    async def test_notes_never_cross_organizations(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        _user, admin_auth = admin
        outsider = await make_user(db, other_organization, "x@meridian.example")
        outsider_auth = AuthorizationContext(
            user_id=outsider.id,
            organization_id=other_organization.id,
            role_keys=("admin",),
            grants={
                "notes.view": Scope.ALL,
                "notes.manage": Scope.ALL,
                "leads.view": Scope.ALL,
                "leads.manage": Scope.ALL,
            },
        )
        foreign_lead = await LeadService(db, outsider_auth).create_lead(
            LeadCreate(first_name="Foreign", last_name="Lead"), outsider
        )
        await NoteService(db, outsider_auth).create_note(
            _create(foreign_lead.id, title="Foreign note"), outsider
        )

        rows, _ = await NoteService(db, admin_auth).list_feed(
            filters=NoteFilters(), limit=50
        )
        assert all(r.title != "Foreign note" for r in rows)

    async def test_rls_blocks_an_unscoped_query(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await NoteService(db, auth).create_note(_create(lead_record.id), user)
        await db.commit()

        from app.db.sql_objects import tenant_policy_statements

        for statement in tenant_policy_statements("notes"):
            await db.execute(text(statement))
        await db.commit()

        try:
            async with db.begin():
                rows = (await db.execute(select(Note))).unique().scalars().all()
            assert rows == [], (
                "An unscoped query returned rows with no tenant context bound. "
                "RLS is not enforcing on notes."
            )
        finally:
            await db.execute(text("DROP POLICY IF EXISTS tenant_isolation ON notes"))
            await db.execute(text("ALTER TABLE notes NO FORCE ROW LEVEL SECURITY"))
            await db.execute(text("ALTER TABLE notes DISABLE ROW LEVEL SECURITY"))
            await db.commit()
