"""Automation end to end: emit, dispatch, execute.

These are the tests that would catch the failures nobody notices until a
customer does.

`TestRecursionGuard` is the most important thing in this file. A workflow's own
writes emit events like any other change, so "when a lead is updated, update the
lead" is a loop whose output is real outbound email. The guard is a marker on
the event and a refusal in the dispatcher, and if it ever stops working the
symptom is not a stack trace — it is a mailbox.

`TestPublishing` covers the other invariant: a published version is immutable
while it runs. Editing a live workflow forks a draft, so a run halfway through
cannot execute the first half of one definition and the second half of another.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.definition import DefinitionError
from app.core.exceptions import ConflictError, PermissionDeniedError
from app.models.automation import (
    Workflow,
    WorkflowEvent,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowVersion,
)
from app.models.task import Task
from app.schemas.automation import WorkflowCreate
from app.schemas.lead import LeadCreate, LeadUpdate
from app.services.automation import AutomationService, DispatchService
from app.services.lead import LeadService
from app.workers.jobs.automation import execute_workflow_run
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


def _definition(nodes: dict, trigger: str = "lead.created", config: dict | None = None) -> dict:  # type: ignore[type-arg]
    return {
        "trigger": {"type": trigger, "config": config or {}},
        "start_node": "n1",
        "nodes": nodes,
    }


def _task_node(title: str = "Follow up {{record.first_name}}") -> dict:  # type: ignore[type-arg]
    return {
        "n1": {
            "type": "action",
            "action": "create_task",
            "label": "Create a task",
            "config": {"title": title, "assignee": "record_owner"},
            "next": None,
        }
    }


async def _published_workflow(db, admin, definition: dict, name: str = "Test flow"):  # type: ignore[no-untyped-def]
    """Create, define, publish and enable a workflow in one step."""
    user, auth = admin
    service = AutomationService(db, auth)
    workflow, _draft = await service.create_workflow(
        WorkflowCreate(
            name=name,
            trigger_type=str(definition["trigger"]["type"]),
            definition=definition,
        ),
        user,
    )
    await service.publish(workflow.id, user)
    await service.set_enabled(workflow.id, True, user)
    return workflow


class TestAuthoring:
    async def test_creating_a_workflow_creates_its_first_draft(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A workflow with no version is a row that cannot be opened."""
        user, auth = admin
        workflow, draft = await AutomationService(db, auth).create_workflow(
            WorkflowCreate(name="New lead follow-up", trigger_type="lead.created"),
            user,
        )
        assert workflow.is_enabled is False
        assert draft.version == 1
        assert draft.status == "draft"

    async def test_a_workflow_starts_disabled(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """One that runs the moment it is saved is one nobody reviews first."""
        user, auth = admin
        workflow, _ = await AutomationService(db, auth).create_workflow(
            WorkflowCreate(name="Draft only", trigger_type="lead.created"), user
        )
        assert workflow.is_enabled is False

    async def test_an_agent_cannot_author_workflows(
        self, db: AsyncSession, organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """A workflow acts on records its author may not otherwise reach, so
        authoring is administrative rather than a wider version of editing your
        own book."""
        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")

        with pytest.raises(PermissionDeniedError):
            await AutomationService(db, agent_auth).create_workflow(
                WorkflowCreate(name="Sneaky", trigger_type="lead.created"), agent
            )

    async def test_a_draft_can_be_saved_incomplete(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Refusing every save until the graph is complete makes the builder
        unusable halfway through building something."""
        user, auth = admin
        service = AutomationService(db, auth)
        workflow, _ = await service.create_workflow(
            WorkflowCreate(name="Half built", trigger_type="lead.created"), user
        )

        draft = await service.save_draft(
            workflow.id,
            {"trigger": {"type": "lead.created"}, "nodes": {"n1": {"type": "action"}}},
            user,
        )
        assert draft.definition["nodes"]["n1"] == {"type": "action"}


class TestPublishing:
    async def test_publishing_validates(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The only place that refuses. A workflow that publishes broken is an
        incident on live customer data."""
        user, auth = admin
        service = AutomationService(db, auth)
        workflow, _ = await service.create_workflow(
            WorkflowCreate(
                name="Broken",
                trigger_type="lead.created",
                definition=_definition({"n1": {"type": "action", "action": "nope"}}),
            ),
            user,
        )

        with pytest.raises(DefinitionError):
            await service.publish(workflow.id, user)

    async def test_publishing_archives_the_previous_version(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Archived, not deleted: a run in flight still points at it."""
        user, auth = admin
        service = AutomationService(db, auth)
        workflow = await _published_workflow(db, admin, _definition(_task_node()))
        first = await service.workflows.published_version(
            workflow.id, auth.organization_id
        )
        assert first is not None

        await service.save_draft(workflow.id, _definition(_task_node("Second")), user)
        second = await service.publish(workflow.id, user)

        await db.refresh(first)
        assert first.status == "archived"
        assert second.version == first.version + 1

    async def test_editing_a_published_workflow_forks_a_draft(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """So a run halfway through cannot execute the first half of one
        definition and the second half of another."""
        user, auth = admin
        service = AutomationService(db, auth)
        workflow = await _published_workflow(db, admin, _definition(_task_node()))

        draft = await service.save_draft(
            workflow.id, _definition(_task_node("Changed")), user
        )
        published = await service.workflows.published_version(
            workflow.id, auth.organization_id
        )

        assert draft.status == "draft"
        assert published is not None and published.id != draft.id
        assert published.definition["nodes"]["n1"]["config"]["title"] != "Changed"

    async def test_a_workflow_cannot_be_enabled_before_publishing(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = AutomationService(db, auth)
        workflow, _ = await service.create_workflow(
            WorkflowCreate(name="Unpublished", trigger_type="lead.created"), user
        )
        with pytest.raises(ConflictError, match="Publish"):
            await service.set_enabled(workflow.id, True, user)

    async def test_deleting_switches_it_off(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A deleted-but-enabled workflow is exactly the thing that keeps
        firing after somebody thought they had stopped it."""
        user, auth = admin
        workflow = await _published_workflow(db, admin, _definition(_task_node()))

        await AutomationService(db, auth).delete_workflow(workflow.id, user)

        row = (
            (await db.execute(select(Workflow).where(Workflow.id == workflow.id)))
            .unique()
            .scalar_one()
        )
        assert row.is_enabled is False
        assert row.deleted_at is not None


class TestEventEmission:
    async def test_creating_a_lead_emits_an_event(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )

        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()
        assert event.event_type == "lead.created"
        assert event.payload["record"]["first_name"] == "Sana"
        assert event.dispatched_at is None

    async def test_an_update_carries_a_before_image_and_a_diff(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Conditions must see the state that triggered them, not whatever the
        record has become by the time a worker picks the event up."""
        user, auth = admin
        service = LeadService(db, auth)
        lead = await service.create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur", stage="new"), user
        )
        await service.update_lead(lead.id, LeadUpdate(stage="qualified"), user)

        events = (
            (
                await db.execute(
                    select(WorkflowEvent).order_by(WorkflowEvent.occurred_at)
                )
            )
            .unique()
            .scalars()
            .all()
        )
        update = events[-1]
        assert update.event_type == "lead.updated"
        assert update.payload["previous"]["stage"] == "new"
        assert update.payload["record"]["stage"] == "qualified"
        assert "stage" in update.payload["changed_fields"]

    async def test_the_snapshot_is_an_explicit_projection(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Reflection over the model would expose whatever gets added to the
        table later — and one of these models has `password_hash`."""
        user, auth = admin
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur", notes="private"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()
        assert "notes" not in event.payload["record"]


class TestDispatch:
    async def test_an_enabled_workflow_gets_a_run(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await _published_workflow(db, admin, _definition(_task_node()))
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()

        runs = await DispatchService(db, organization.id).dispatch(event)

        assert len(runs) == 1
        assert runs[0].status == "pending"
        assert event.dispatched_at is not None

    async def test_a_disabled_workflow_gets_nothing(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        workflow = await _published_workflow(db, admin, _definition(_task_node()))
        await AutomationService(db, auth).set_enabled(workflow.id, False, user)

        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()

        assert await DispatchService(db, organization.id).dispatch(event) == []

    async def test_an_unmatched_event_is_still_marked_dispatched(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Leaving it NULL would have the sweep re-examine it forever."""
        user, auth = admin
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()

        await DispatchService(db, organization.id).dispatch(event)
        assert event.dispatched_at is not None

    async def test_trigger_narrowing_prevents_a_run(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Narrowed out before a run exists, so the log does not fill with runs
        the workflow immediately abandons."""
        user, auth = admin
        await _published_workflow(
            db,
            admin,
            _definition(_task_node(), trigger="lead.updated", config={"fields": "stage"}),
        )
        service = LeadService(db, auth)
        lead = await service.create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        await service.update_lead(lead.id, LeadUpdate(preferred_location="Noe"), user)

        events = (
            (await db.execute(select(WorkflowEvent))).unique().scalars().all()
        )
        update = next(e for e in events if e.event_type == "lead.updated")
        assert await DispatchService(db, organization.id).dispatch(update) == []


class TestRecursionGuard:
    """The guard whose failure mode is a mailbox, not a stack trace."""

    async def test_an_automation_caused_change_never_dispatches(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        from app.automation.events import emit

        await _published_workflow(db, admin, _definition(_task_node()))
        event = await emit(
            db,
            organization_id=organization.id,
            event_type="lead.created",
            entity_type="lead",
            record={"id": "x"},
            by_automation=True,
        )

        assert await DispatchService(db, organization.id).dispatch(event) == []
        assert event.dispatched_at is not None

    async def test_a_system_context_is_recognised_as_automation(self) -> None:
        """The marker comes from the authorization context rather than a flag
        threaded through every service call, so a service cannot forget it."""
        from app.automation.emitter import is_automation_actor

        assert is_automation_actor(("system",)) is True
        assert is_automation_actor(("admin",)) is False


class TestExecution:
    async def test_a_run_creates_the_task_and_succeeds(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The whole loop: a lead is created, a workflow fires, a real task
        lands through the real service."""
        user, auth = admin
        await _published_workflow(db, admin, _definition(_task_node()))
        lead = await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()
        runs = await DispatchService(db, organization.id).dispatch(event)
        await db.commit()

        assert await execute_workflow_run({}, str(runs[0].id), str(organization.id)) == (
            "succeeded"
        )

        task = (
            (await db.execute(select(Task).where(Task.entity_id == lead.id)))
            .unique()
            .scalar_one()
        )
        assert task.title == "Follow up Sana"

    async def test_every_step_is_logged(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """"Why did this client get that email" has to have an answer."""
        user, auth = admin
        await _published_workflow(db, admin, _definition(_task_node()))
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()
        runs = await DispatchService(db, organization.id).dispatch(event)
        await db.commit()
        await execute_workflow_run({}, str(runs[0].id), str(organization.id))

        step = (
            (await db.execute(select(WorkflowRunStep))).unique().scalar_one()
        )
        assert step.node_type == "action"
        assert step.status == "succeeded"
        # Copied at execution time, so a later edit cannot rewrite history.
        assert step.node_label == "Create a task"
        assert "task_id" in step.output

    async def test_a_condition_that_fails_ends_the_run_without_acting(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        definition = _definition(
            {
                "n1": {
                    "type": "condition",
                    "label": "Is it hot?",
                    "mode": "all",
                    "comparisons": [
                        {"field": "temperature", "operator": "equals", "value": "hot"}
                    ],
                    "on_true": "n2",
                    "on_false": None,
                },
                "n2": _task_node()["n1"],
            }
        )
        await _published_workflow(db, admin, definition)
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur", temperature="cold"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()
        runs = await DispatchService(db, organization.id).dispatch(event)
        await db.commit()

        assert await execute_workflow_run({}, str(runs[0].id), str(organization.id)) == (
            "succeeded"
        )
        assert (await db.execute(select(Task))).unique().scalars().all() == []

    async def test_a_delay_parks_the_run_without_holding_a_worker(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The only shape that survives "wait three days"."""
        user, auth = admin
        definition = _definition(
            {
                "n1": {
                    "type": "delay",
                    "label": "Wait a day",
                    "config": {"minutes": 1440},
                    "next": "n2",
                },
                "n2": _task_node()["n1"],
            }
        )
        await _published_workflow(db, admin, definition)
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()
        runs = await DispatchService(db, organization.id).dispatch(event)
        run_id = runs[0].id
        await db.commit()

        assert await execute_workflow_run({}, str(run_id), str(organization.id)) == (
            "waiting"
        )

        run = (
            (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)))
            .unique()
            .scalar_one()
        )
        assert run.resume_at is not None
        assert run.resume_at > datetime.now(UTC) + timedelta(hours=23)
        # Parked *after* the delay, or waking would re-park immediately.
        assert run.current_node_id == "n2"
        assert (await db.execute(select(Task))).unique().scalars().all() == []

    async def test_a_finished_run_is_never_re_executed(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A duplicate delivery must not send a customer the same thing twice —
        workflow actions are not idempotent."""
        user, auth = admin
        await _published_workflow(db, admin, _definition(_task_node()))
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()
        runs = await DispatchService(db, organization.id).dispatch(event)
        run_id = runs[0].id
        await db.commit()

        await execute_workflow_run({}, str(run_id), str(organization.id))
        assert await execute_workflow_run({}, str(run_id), str(organization.id)) == (
            "succeeded"
        )

        tasks = (await db.execute(select(Task))).unique().scalars().all()
        assert len(tasks) == 1

    async def test_a_failing_action_stops_the_run_and_records_why(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The next step almost always assumes this one happened."""
        user, auth = admin
        definition = _definition(
            {
                "n1": {
                    "type": "action",
                    "action": "send_email",
                    "label": "Email them",
                    "config": {"subject": "Hi", "body": "Hello"},
                    "next": "n2",
                },
                "n2": _task_node()["n1"],
            }
        )
        await _published_workflow(db, admin, definition)
        # No email address, so the action diagnoses its own failure.
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()
        runs = await DispatchService(db, organization.id).dispatch(event)
        run_id = runs[0].id
        await db.commit()

        assert await execute_workflow_run({}, str(run_id), str(organization.id)) == (
            "failed"
        )

        run = (
            (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)))
            .unique()
            .scalar_one()
        )
        assert run.error and "email" in run.error.lower()
        # The step after the failure did not run.
        assert (await db.execute(select(Task))).unique().scalars().all() == []


class TestRunHistory:
    async def test_runs_are_listed_for_the_workflow(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        workflow = await _published_workflow(db, admin, _definition(_task_node()))
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()
        await DispatchService(db, organization.id).dispatch(event)

        runs = await AutomationService(db, auth).list_runs(workflow_id=workflow.id)
        assert len(runs) == 1

    async def test_only_a_waiting_run_can_be_cancelled(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A `running` one is inside a worker, and a flag it never checks is
        not a cancellation."""
        user, auth = admin
        await _published_workflow(db, admin, _definition(_task_node()))
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()
        runs = await DispatchService(db, organization.id).dispatch(event)

        with pytest.raises(ConflictError, match="waiting"):
            await AutomationService(db, auth).cancel_run(runs[0].id, user)


class TestTenantIsolation:
    async def test_rls_blocks_an_unscoped_workflow_query(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        await _published_workflow(db, admin, _definition(_task_node()))
        await db.commit()

        from sqlalchemy import text

        from app.db.sql_objects import tenant_policy_statements

        for statement in tenant_policy_statements("workflows"):
            await db.execute(text(statement))
        await db.commit()

        try:
            async with db.begin():
                rows = (await db.execute(select(Workflow))).unique().scalars().all()
            assert rows == [], (
                "An unscoped query returned workflows with no tenant context "
                "bound. RLS is not enforcing."
            )
        finally:
            await db.execute(
                text("DROP POLICY IF EXISTS tenant_isolation ON workflows")
            )
            await db.execute(text("ALTER TABLE workflows NO FORCE ROW LEVEL SECURITY"))
            await db.execute(text("ALTER TABLE workflows DISABLE ROW LEVEL SECURITY"))
            await db.commit()


class TestVersionPinning:
    async def test_a_run_executes_the_version_it_started_on(
        self, db: AsyncSession, organization, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Editing a live workflow must not change what an in-flight run does
        halfway through."""
        user, auth = admin
        service = AutomationService(db, auth)
        workflow = await _published_workflow(
            db, admin, _definition(_task_node("Original"))
        )
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Sana", last_name="Kaur"), user
        )
        event = (await db.execute(select(WorkflowEvent))).unique().scalar_one()
        runs = await DispatchService(db, organization.id).dispatch(event)
        pinned = runs[0].version_id

        await service.save_draft(workflow.id, _definition(_task_node("Rewritten")), user)
        await service.publish(workflow.id, user)
        await db.commit()

        await execute_workflow_run({}, str(runs[0].id), str(organization.id))

        task = (await db.execute(select(Task))).unique().scalar_one()
        assert task.title == "Original"
        version = (
            (
                await db.execute(
                    select(WorkflowVersion).where(WorkflowVersion.id == pinned)
                )
            )
            .unique()
            .scalar_one()
        )
        assert version.status == "archived"
