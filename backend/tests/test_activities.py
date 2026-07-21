"""Activities — the user-facing business timeline.

The load-bearing property, and most of what this file proves, is that an
activity **has no scope anchor of its own**. Its visibility follows the record
it hangs off, resolved through `EntityAccess`:

  * a per-entity timeline read requires being able to read that record — if you
    can see the lead, you can see what happened to it, whoever logged it;
  * the cross-entity feed is gated on `activities.view`, whose scope decides
    whose activity is visible;
  * writing requires `activities.manage` *and* readability of the parent, so a
    caller cannot log into a timeline they cannot see.

Two further rules: system-written entries (`stage_change`) are immutable — a
funnel event a user can rewrite is not evidence — and a logged activity records
something that already happened, so a future `occurred_at` is refused (that is
what tasks are for).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.activity import Activity
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.schemas.activity import ActivityCreate, ActivityFilters, ActivityUpdate
from app.schemas.lead import LeadCreate
from app.services.activity import ActivityService, is_system_activity
from app.services.lead import LeadService
from app.services.rbac import AuthorizationContext
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


@pytest.fixture
async def lead_record(db: AsyncSession, admin):  # type: ignore[no-untyped-def]
    """A lead owned by admin, used as the parent for timeline tests."""
    user, auth = admin
    return await LeadService(db, auth).create_lead(
        LeadCreate(first_name="Dara", last_name="Okafor"), user
    )


def _create(entity_id, **overrides: object) -> ActivityCreate:  # type: ignore[no-untyped-def]
    data: dict = {
        "entity_type": "lead",
        "entity_id": entity_id,
        "type": "call",
        "subject": "Left a voicemail",
        **overrides,
    }
    return ActivityCreate(**data)


# --------------------------------------------------------------- manual logging


class TestLogging:
    async def test_logging_writes_the_entry_and_audits(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        activity = await ActivityService(db, auth).create_activity(
            _create(lead_record.id), user
        )
        assert activity.type == "call"
        assert activity.actor_id == user.id

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_CREATED)
            )
        ).scalars().all()
        assert any(e.entity_type == "activity" for e in entry)

    async def test_cannot_log_against_a_record_you_cannot_see(
        self, db: AsyncSession, organization: Organization, rbac_seeded, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        """Logging into a timeline you cannot read would let you write into
        someone else's record. 404, not 403 — the id must not be confirmable."""
        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        with pytest.raises(NotFoundError):
            await ActivityService(db, agent_auth).create_activity(
                _create(lead_record.id), agent
            )

    async def test_requires_the_manage_permission(
        self, db: AsyncSession, admin, organization: Organization, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        """View-only on the parent is not enough to write to its timeline."""
        viewer = await make_user(db, organization, "viewer@vantage.example")
        viewer_auth = AuthorizationContext(
            user_id=viewer.id,
            organization_id=organization.id,
            role_keys=("viewer",),
            # Can see every lead, but holds no activities.manage.
            grants={"leads.view": Scope.ALL, "activities.view": Scope.ALL},
        )
        with pytest.raises(PermissionDeniedError):
            await ActivityService(db, viewer_auth).create_activity(
                _create(lead_record.id), viewer
            )

    async def test_a_future_occurred_at_is_rejected(self, lead_record) -> None:  # type: ignore[no-untyped-def]
        """A timeline is not a schedule — that is what tasks are for."""
        future = datetime.now(UTC) + timedelta(days=1)
        with pytest.raises(ValueError, match="future"):
            _create(lead_record.id, occurred_at=future)

    async def test_a_backdated_occurred_at_is_allowed(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        """A call logged on Monday may have happened on Friday."""
        user, auth = admin
        past = datetime.now(UTC) - timedelta(days=3)
        activity = await ActivityService(db, auth).create_activity(
            _create(lead_record.id, occurred_at=past), user
        )
        assert activity.occurred_at == past

    async def test_stage_change_cannot_be_logged_by_a_person(self, lead_record) -> None:  # type: ignore[no-untyped-def]
        """Accepting it from a client would let anyone forge a funnel event."""
        with pytest.raises(ValueError):
            ActivityCreate(
                entity_type="lead",
                entity_id=lead_record.id,
                type="stage_change",  # not in ManualActivityType
                subject="fake",
            )


# ------------------------------------------------------ timeline vs. the feed


class TestReadAuthorization:
    async def test_timeline_read_follows_the_parent(
        self, db: AsyncSession, admin, organization: Organization, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        """An agent who cannot read the lead cannot read its timeline, even
        with activities.view — visibility follows the record."""
        user, auth = admin
        await ActivityService(db, auth).create_activity(_create(lead_record.id), user)

        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        with pytest.raises(NotFoundError):
            await ActivityService(db, agent_auth).list_for_entity(
                entity_type="lead", entity_id=lead_record.id
            )

    async def test_timeline_is_visible_to_whoever_can_read_the_record(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await ActivityService(db, auth).create_activity(_create(lead_record.id), user)
        rows, _ = await ActivityService(db, auth).list_for_entity(
            entity_type="lead", entity_id=lead_record.id
        )
        assert len(rows) == 1

    async def test_feed_is_actor_scoped(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """Two agents, each with their own lead. Each feed shows only its own
        author's activity — an agent reading a feed reads their own work."""
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
        await ActivityService(db, one_auth).create_activity(
            _create(lead_one.id, subject="One's call"), one
        )
        await ActivityService(db, two_auth).create_activity(
            _create(lead_two.id, subject="Two's call"), two
        )

        rows, _ = await ActivityService(db, one_auth).list_feed(
            filters=ActivityFilters()
        )
        assert [r.subject for r in rows] == ["One's call"]

    async def test_filtering_the_feed_by_a_foreign_actor_is_empty_not_error(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """Asking for an actor outside your scope matches nothing — the same
        shape as any filter that hits no rows, and it does not confirm the user
        exists."""
        one = await make_user(db, organization, "one@vantage.example")
        two = await make_user(db, organization, "two@vantage.example")
        one_auth = await auth_for(db, one, "agent")

        rows, has_more = await ActivityService(db, one_auth).list_feed(
            filters=ActivityFilters(actor_id=two.id)
        )
        assert rows == []
        assert has_more is False


# ------------------------------------------------------- corrections & system


class TestCorrections:
    async def test_correcting_your_own_entry(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ActivityService(db, auth)
        activity = await service.create_activity(_create(lead_record.id), user)

        updated = await service.update_activity(
            activity.id, ActivityUpdate(subject="Spoke with the buyer"), user
        )
        assert updated.subject == "Spoke with the buyer"

    async def test_agent_cannot_edit_someone_elses_entry(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """A colleague rewriting your record of a call is not a correction. The
        parent must be shared for the read to even reach the own-check, so this
        uses a property — shared inventory both agents can see."""
        from app.schemas.property import PropertyCreate
        from app.services.property import PropertyService

        one = await make_user(db, organization, "one@vantage.example")
        two = await make_user(db, organization, "two@vantage.example")
        one_auth = await auth_for(db, one, "agent")
        two_auth = await auth_for(db, two, "agent")

        listing = await PropertyService(db, one_auth).create_property(
            PropertyCreate(
                title="123 Main",
                address_line1="123 Main St",
                city="Austin",
                state="TX",
                postal_code="78701",
                property_type="single_family",
            ),
            one,
        )
        activity = await ActivityService(db, one_auth).create_activity(
            _create(listing.id, entity_type="property"), one
        )

        with pytest.raises(PermissionDeniedError):
            await ActivityService(db, two_auth).update_activity(
                activity.id, ActivityUpdate(subject="hijacked"), two
            )

    async def test_system_activity_cannot_be_edited(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ActivityService(db, auth)
        # The system path: record a stage_change directly, as the deal
        # transition would.
        system = await service.record(
            organization_id=auth.organization_id,
            actor_id=user.id,
            entity_type="lead",
            entity_id=lead_record.id,
            type="stage_change",
            subject="Moved to Showing",
        )
        assert is_system_activity(system)
        with pytest.raises(ConflictError, match="System-recorded"):
            await service.update_activity(
                system.id, ActivityUpdate(subject="tampered"), user
            )

    async def test_system_activity_cannot_be_deleted(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ActivityService(db, auth)
        system = await service.record(
            organization_id=auth.organization_id,
            actor_id=user.id,
            entity_type="lead",
            entity_id=lead_record.id,
            type="stage_change",
            subject="Moved to Offer",
        )
        with pytest.raises(ConflictError, match="System-recorded"):
            await service.delete_activity(system.id, user)

    async def test_delete_is_hard_and_audits(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        """activities has no deleted_at — a timeline with hidden rows is worse
        to reason about than one without. The audit entry outlives the row."""
        user, auth = admin
        service = ActivityService(db, auth)
        activity = await service.create_activity(_create(lead_record.id), user)
        await service.delete_activity(activity.id, user)

        remaining = (
            await db.execute(select(func.count()).select_from(Activity))
        ).scalar()
        assert remaining == 0

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_DELETED)
            )
        ).scalar_one()
        assert entry.entity_type == "activity"


# --------------------------------------------------------------- search / feed


class TestSearch:
    async def test_search_matches_subject_and_body(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ActivityService(db, auth)
        await service.create_activity(
            _create(lead_record.id, subject="Discussed financing options"), user
        )
        await service.create_activity(
            _create(lead_record.id, subject="Scheduled a showing"), user
        )

        rows, _ = await service.list_feed(
            filters=ActivityFilters(search="financing")
        )
        assert [r.subject for r in rows] == ["Discussed financing options"]


# --------------------------------------------------------- tenant isolation


class TestTenantIsolation:
    async def test_rls_blocks_an_unscoped_query(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await ActivityService(db, auth).create_activity(_create(lead_record.id), user)
        await db.commit()

        from app.db.sql_objects import tenant_policy_statements

        for statement in tenant_policy_statements("activities"):
            await db.execute(text(statement))
        await db.commit()

        try:
            async with db.begin():
                rows = (await db.execute(select(Activity))).unique().scalars().all()
            assert rows == [], (
                "An unscoped query returned rows with no tenant context bound. "
                "RLS is not enforcing on activities."
            )
        finally:
            await db.execute(
                text("DROP POLICY IF EXISTS tenant_isolation ON activities")
            )
            await db.execute(
                text("ALTER TABLE activities NO FORCE ROW LEVEL SECURITY")
            )
            await db.execute(
                text("ALTER TABLE activities DISABLE ROW LEVEL SECURITY")
            )
            await db.commit()
