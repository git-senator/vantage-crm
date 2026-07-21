"""Tasks — the last CRM entity, and the one with the loosest coupling.

The CRUD shape is identical to every entity before it and is not re-proven here.
What is specific to tasks, and what this file is about:

  * **the scope anchor is `assignee_id`, not `owner_id`** — a task belongs to
    whoever has to do it, so an agent sees the work assigned to them regardless
    of who asked;
  * **completing is a domain action**, not a field edit — `POST .../complete`
    stamps `completed_at`, writes onto the linked record's timeline and audits,
    and a PATCH to `status='done'` is refused because it would skip all of that
    and violate `ck_tasks_completed_at`;
  * **a task hangs off any entity, or off none** — a linked task writes to that
    record's timeline, a standalone one has nowhere to write and must not try;
  * **assignment respects scope** — you cannot assign work outside the reach of
    your `tasks.manage` grant, and doing so fires the notification seam.
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
from app.models.rbac import Team, TeamMember
from app.models.task import Task
from app.schemas.lead import LeadCreate
from app.schemas.task import TaskCreate, TaskFilters, TaskUpdate
from app.services.lead import LeadService
from app.services.rbac import AuthorizationContext
from app.services.task import TaskService
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


@pytest.fixture
async def lead_record(db: AsyncSession, admin):  # type: ignore[no-untyped-def]
    user, auth = admin
    return await LeadService(db, auth).create_lead(
        LeadCreate(first_name="Priya", last_name="Nadella"), user
    )


def _payload(**overrides: object) -> TaskCreate:
    data: dict = {"title": "Call the title company back", **overrides}
    return TaskCreate(**data)


# --------------------------------------------------------------------- create


class TestCreate:
    async def test_defaults_the_assignee_to_the_creator(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        task = await TaskService(db, auth).create_task(_payload(), user)
        assert task.assignee_id == user.id
        assert task.created_by == user.id
        assert task.status == "todo"

    async def test_a_standalone_task_writes_no_activity(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """"Call the title company back" hangs off nothing; there is nowhere
        to log it, and the service must not invent a timeline for it."""
        user, auth = admin
        await TaskService(db, auth).create_task(_payload(), user)
        count = (
            await db.execute(select(func.count()).select_from(Activity))
        ).scalar()
        assert count == 0

    async def test_a_linked_task_logs_on_the_records_timeline(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        task = await TaskService(db, auth).create_task(
            _payload(title="Follow up", entity_type="lead", entity_id=lead_record.id),
            user,
        )
        activity = (
            await db.execute(
                select(Activity).where(Activity.entity_id == lead_record.id)
            )
        ).unique().scalar_one()
        assert activity.entity_type == "lead"
        assert "Follow up" in activity.subject
        assert activity.metadata_["task_id"] == str(task.id)
        assert activity.metadata_["source"] == "task"

    async def test_cannot_attach_to_a_record_you_cannot_see(
        self, db: AsyncSession, organization: Organization, rbac_seeded, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        """The lead belongs to admin; an agent cannot pin work to it."""
        agent_user = await make_user(db, organization, "loner@vantage.example")
        agent_auth = await auth_for(db, agent_user, "agent")
        with pytest.raises(NotFoundError):
            await TaskService(db, agent_auth).create_task(
                _payload(entity_type="lead", entity_id=lead_record.id), agent_user
            )

    async def test_half_an_entity_link_is_rejected_at_the_schema(self) -> None:
        with pytest.raises(ValueError, match="together"):
            TaskCreate(title="x", entity_type="lead")

    async def test_requires_the_manage_permission(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        viewer = await make_user(db, organization, "viewer@vantage.example")
        viewer_auth = AuthorizationContext(
            user_id=viewer.id,
            organization_id=organization.id,
            role_keys=("viewer",),
            grants={"tasks.view": Scope.OWN},
        )
        with pytest.raises(PermissionDeniedError):
            await TaskService(db, viewer_auth).create_task(_payload(), viewer)


# ------------------------------------------------------------------- complete


class TestCompletion:
    async def test_complete_stamps_the_timestamp_with_the_status(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = TaskService(db, auth)
        task = await service.create_task(_payload(), user)

        done = await service.complete_task(task.id, user)
        assert done.status == "done"
        assert done.completed_at is not None
        assert done.is_done is True

    async def test_patch_to_done_is_refused(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A PATCH would skip the timestamp, the activity and the audit entry,
        and leave ck_tasks_completed_at unsatisfiable."""
        user, auth = admin
        service = TaskService(db, auth)
        task = await service.create_task(_payload(), user)
        with pytest.raises(ConflictError, match="complete endpoint"):
            await service.update_task(task.id, TaskUpdate(status="done"), user)

    async def test_completing_twice_is_a_conflict(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = TaskService(db, auth)
        task = await service.create_task(_payload(), user)
        await service.complete_task(task.id, user)
        with pytest.raises(ConflictError, match="already complete"):
            await service.complete_task(task.id, user)

    async def test_complete_logs_on_the_linked_timeline(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = TaskService(db, auth)
        task = await service.create_task(
            _payload(entity_type="lead", entity_id=lead_record.id), user
        )
        await service.complete_task(task.id, user, note="Docs received")

        subjects = [
            a.subject
            for a in (
                await db.execute(
                    select(Activity)
                    .where(Activity.entity_id == lead_record.id)
                    .order_by(Activity.created_at)
                )
            )
            .unique()
            .scalars()
            .all()
        ]
        assert any("completed" in s.lower() for s in subjects)

    async def test_complete_audits_with_a_dedicated_action(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """"What got done this week" is a question worth asking directly, so it
        is not buried in a generic record.updated diff."""
        user, auth = admin
        service = TaskService(db, auth)
        task = await service.create_task(_payload(), user)
        await service.complete_task(task.id, user)

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_COMPLETED)
            )
        ).scalar_one()
        assert entry.entity_type == "task"

    async def test_reopen_clears_the_timestamp(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = TaskService(db, auth)
        task = await service.create_task(_payload(), user)
        await service.complete_task(task.id, user)

        reopened = await service.reopen_task(task.id, user)
        assert reopened.status == "todo"
        assert reopened.completed_at is None

    async def test_reopening_an_open_task_is_a_conflict(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = TaskService(db, auth)
        task = await service.create_task(_payload(), user)
        with pytest.raises(ConflictError, match="not complete"):
            await service.reopen_task(task.id, user)

    async def test_patch_reopen_also_clears_the_timestamp(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Moving off `done` through a PATCH is allowed — the timestamp must
        follow, or the row violates ck_tasks_completed_at on the next flush."""
        user, auth = admin
        service = TaskService(db, auth)
        task = await service.create_task(_payload(), user)
        await service.complete_task(task.id, user)

        moved = await service.update_task(
            task.id, TaskUpdate(status="in_progress"), user
        )
        assert moved.status == "in_progress"
        assert moved.completed_at is None


# ---------------------------------------------------------------- assignment


class TestAssignment:
    async def test_agent_cannot_assign_to_a_teammate(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """An agent holds tasks.manage at OWN scope — self only."""
        agent = await make_user(db, organization, "a@vantage.example")
        other = await make_user(db, organization, "b@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")

        with pytest.raises(PermissionDeniedError, match="yourself"):
            await TaskService(db, agent_auth).create_task(
                _payload(assignee_id=other.id), agent
            )

    async def test_manager_can_assign_within_the_team(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        manager = await make_user(db, organization, "mgr@vantage.example")
        member = await make_user(db, organization, "mem@vantage.example")
        team = Team(organization_id=organization.id, name="Eastside")
        db.add(team)
        await db.flush()
        for u in (manager, member):
            db.add(TeamMember(team_id=team.id, user_id=u.id, organization_id=organization.id))
        await db.flush()

        manager_auth = await auth_for(db, manager, "manager")
        task = await TaskService(db, manager_auth).create_task(
            _payload(assignee_id=member.id), manager
        )
        assert task.assignee_id == member.id

    async def test_manager_cannot_assign_outside_the_team(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        manager = await make_user(db, organization, "mgr@vantage.example")
        outsider = await make_user(db, organization, "solo@vantage.example")
        team = Team(organization_id=organization.id, name="Eastside")
        db.add(team)
        await db.flush()
        db.add(TeamMember(team_id=team.id, user_id=manager.id, organization_id=organization.id))
        await db.flush()

        manager_auth = await auth_for(db, manager, "manager")
        with pytest.raises(PermissionDeniedError, match="team"):
            await TaskService(db, manager_auth).create_task(
                _payload(assignee_id=outsider.id), manager
            )

    async def test_assigning_to_another_tenant_member_is_not_found(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """An id from another tenant would be written and then hidden by RLS —
        work silently lost. It is refused as 404 before it can land."""
        _user, auth = admin
        outsider = await make_user(db, other_organization, "x@meridian.example")
        with pytest.raises(NotFoundError):
            await TaskService(db, auth).create_task(
                _payload(assignee_id=outsider.id), _user
            )

    async def test_reassignment_fires_the_notification_seam(
        self, db: AsyncSession, organization: Organization, rbac_seeded, caplog
    ) -> None:  # type: ignore[no-untyped-def]
        """The seam logs a pending notification rather than sending — delivery
        is Phase 3 queued work. Assignment must reach it exactly once."""
        import logging

        manager = await make_user(db, organization, "mgr@vantage.example")
        member = await make_user(db, organization, "mem@vantage.example")
        team = Team(organization_id=organization.id, name="Eastside")
        db.add(team)
        await db.flush()
        for u in (manager, member):
            db.add(TeamMember(team_id=team.id, user_id=u.id, organization_id=organization.id))
        await db.flush()

        manager_auth = await auth_for(db, manager, "manager")
        service = TaskService(db, manager_auth)
        task = await service.create_task(_payload(), manager)

        with caplog.at_level(logging.INFO):
            await service.assign_task(task.id, member.id, manager)

        assert any(
            r.message == "task_assignment_pending_notification"
            for r in caplog.records
        )


# ------------------------------------------------------------ scope as SQL


class TestScopeIsAWhereClause:
    async def test_assignee_not_owner_is_the_anchor(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """A manager creates work for an agent. The agent — who did not create
        it — must see it; scoping on `created_by` would hide their own queue."""
        manager = await make_user(db, organization, "mgr@vantage.example")
        member = await make_user(db, organization, "mem@vantage.example")
        team = Team(organization_id=organization.id, name="Eastside")
        db.add(team)
        await db.flush()
        for u in (manager, member):
            db.add(TeamMember(team_id=team.id, user_id=u.id, organization_id=organization.id))
        await db.flush()

        manager_auth = await auth_for(db, manager, "manager")
        member_auth = await auth_for(db, member, "agent")
        await TaskService(db, manager_auth).create_task(
            _payload(title="Prep the CMA", assignee_id=member.id), manager
        )

        rows, _ = await TaskService(db, member_auth).list_tasks(
            filters=TaskFilters(), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Prep the CMA"]

    async def test_agent_does_not_see_a_colleagues_task(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        one = await make_user(db, organization, "one@vantage.example")
        two = await make_user(db, organization, "two@vantage.example")
        one_auth = await auth_for(db, one, "agent")
        two_auth = await auth_for(db, two, "agent")

        await TaskService(db, one_auth).create_task(_payload(title="Mine"), one)
        rows, _ = await TaskService(db, two_auth).list_tasks(
            filters=TaskFilters(), limit=50, cursor=None
        )
        assert all(r.title != "Mine" for r in rows)

    async def test_out_of_scope_task_is_404_not_403(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        one = await make_user(db, organization, "one@vantage.example")
        two = await make_user(db, organization, "two@vantage.example")
        one_auth = await auth_for(db, one, "agent")
        two_auth = await auth_for(db, two, "agent")

        task = await TaskService(db, one_auth).create_task(_payload(), one)
        with pytest.raises(NotFoundError):
            await TaskService(db, two_auth).get_task(task.id)


# ------------------------------------------------------------- overdue/filters


class TestFiltersAndQueue:
    async def test_overdue_is_a_predicate_not_a_column(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = TaskService(db, auth)
        past = datetime.now(UTC) - timedelta(days=1)
        future = datetime.now(UTC) + timedelta(days=1)
        await service.create_task(_payload(title="Late", due_at=past), user)
        await service.create_task(_payload(title="Soon", due_at=future), user)

        rows, _ = await service.list_tasks(
            filters=TaskFilters(overdue=True), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Late"]

    async def test_a_completed_task_is_never_overdue(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Past due but finished is not overdue — the derived flag has to read
        the status, not just the clock."""
        user, auth = admin
        service = TaskService(db, auth)
        past = datetime.now(UTC) - timedelta(days=1)
        task = await service.create_task(_payload(due_at=past), user)
        await service.complete_task(task.id, user)

        rows, _ = await service.list_tasks(
            filters=TaskFilters(overdue=True), limit=50, cursor=None
        )
        assert rows == []

    async def test_queue_orders_by_due_date_undated_last(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A work queue sorted by creation date is useless; undated work is not
        more urgent than work due today."""
        user, auth = admin
        service = TaskService(db, auth)
        soon = datetime.now(UTC) + timedelta(hours=1)
        later = datetime.now(UTC) + timedelta(days=3)
        await service.create_task(_payload(title="NoDate"), user)
        await service.create_task(_payload(title="Later", due_at=later), user)
        await service.create_task(_payload(title="Soon", due_at=soon), user)

        rows = await service.queue(filters=TaskFilters(), limit=25)
        assert [r.title for r in rows] == ["Soon", "Later", "NoDate"]

    async def test_status_counts_group_by_status(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = TaskService(db, auth)
        a = await service.create_task(_payload(title="a"), user)
        await service.create_task(_payload(title="b"), user)
        await service.complete_task(a.id, user)

        counts = await service.status_counts()
        assert counts.get("done") == 1
        assert counts.get("todo") == 1

    async def test_search_matches_title(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = TaskService(db, auth)
        await service.create_task(_payload(title="Schedule inspection"), user)
        await service.create_task(_payload(title="Order appraisal"), user)

        rows, _ = await service.list_tasks(
            filters=TaskFilters(search="inspection"), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Schedule inspection"]


# --------------------------------------------------------------- soft delete


class TestDelete:
    async def test_delete_is_soft_and_leaves_the_audit_trail(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = TaskService(db, auth)
        task = await service.create_task(_payload(), user)
        await service.delete_task(task.id, user)

        with pytest.raises(NotFoundError):
            await service.get_task(task.id)

        row = (
            await db.execute(select(Task).where(Task.id == task.id))
        ).unique().scalar_one()
        assert row.deleted_at is not None

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_DELETED)
            )
        ).scalar_one()
        assert entry.entity_type == "task"


# --------------------------------------------------------- tenant isolation


class TestTenantIsolation:
    async def test_tasks_never_cross_organizations(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        _user, admin_auth = admin
        outsider = await make_user(db, other_organization, "x@meridian.example")
        outsider_auth = AuthorizationContext(
            user_id=outsider.id,
            organization_id=other_organization.id,
            role_keys=("admin",),
            grants={"tasks.view": Scope.ALL, "tasks.manage": Scope.ALL},
        )
        await TaskService(db, outsider_auth).create_task(
            _payload(title="Foreign"), outsider
        )

        rows, _ = await TaskService(db, admin_auth).list_tasks(
            filters=TaskFilters(), limit=50, cursor=None
        )
        assert all(r.title != "Foreign" for r in rows)

    async def test_rls_blocks_an_unscoped_query(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await TaskService(db, auth).create_task(_payload(), user)
        await db.commit()

        from app.db.sql_objects import tenant_policy_statements

        for statement in tenant_policy_statements("tasks"):
            await db.execute(text(statement))
        await db.commit()

        try:
            async with db.begin():
                rows = (await db.execute(select(Task))).unique().scalars().all()
            assert rows == [], (
                "An unscoped query returned rows with no tenant context bound. "
                "RLS is not enforcing on tasks."
            )
        finally:
            await db.execute(text("DROP POLICY IF EXISTS tenant_isolation ON tasks"))
            await db.execute(text("ALTER TABLE tasks NO FORCE ROW LEVEL SECURITY"))
            await db.execute(text("ALTER TABLE tasks DISABLE ROW LEVEL SECURITY"))
            await db.commit()
