"""Workflow authoring, publishing, and dispatch.

Two services with different callers and different rules.

`AutomationService` is the authoring side: an admin creating, editing and
publishing workflows through the API. Everything it does is gated on
`automations.manage`, and its central rule is that **a published version is
immutable**. Editing a published workflow forks a new draft rather than mutating
what is running.

`DispatchService` is the machine side: it turns outbox events into runs. It has
no authorization context because it has no user — it runs in the worker, tenant
by tenant, exactly like the sweeps in Phase 3.2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.definition import (
    DefinitionError,
    parse,
    validate,
    validate_or_raise,
)
from app.automation.events import was_caused_by_automation
from app.automation.triggers import TRIGGERS, trigger_matches
from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.automation import (
    Workflow,
    WorkflowEvent,
    WorkflowRun,
    WorkflowVersion,
)
from app.models.user import User
from app.repositories.automation import WorkflowRepository, WorkflowRunRepository
from app.schemas.automation import WorkflowCreate, WorkflowUpdate
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext

logger = get_logger(__name__)

ENTITY_TYPE = "workflow"


class AutomationService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.workflows = WorkflowRepository(session)
        self.runs = WorkflowRunRepository(session)
        self.audit = AuditService(session)

    # ---------------------------------------------------------------- read

    async def list_workflows(self) -> list[Workflow]:
        self.auth.require("automations.view")
        return await self.workflows.list_all(self.auth.organization_id)

    async def get_workflow(self, workflow_id: UUID) -> Workflow:
        self.auth.require("automations.view")
        workflow = await self.workflows.get(workflow_id, self.auth.organization_id)
        if workflow is None:
            raise NotFoundError("Workflow not found.")
        return workflow

    async def get_version(self, workflow_id: UUID, version_id: UUID) -> WorkflowVersion:
        self.auth.require("automations.view")
        version = await self.workflows.get_version(
            version_id, self.auth.organization_id
        )
        if version is None or version.workflow_id != workflow_id:
            raise NotFoundError("Version not found.")
        return version

    async def latest_draft(self, workflow_id: UUID) -> WorkflowVersion | None:
        self.auth.require("automations.view")
        return await self.workflows.latest_draft(
            workflow_id, self.auth.organization_id
        )

    # --------------------------------------------------------------- write

    async def create_workflow(
        self, payload: WorkflowCreate, actor: User
    ) -> tuple[Workflow, WorkflowVersion]:
        """A new workflow and its first draft, in one step.

        A workflow with no version is a row that cannot be opened in the
        builder, so creating one always creates a draft to edit.
        """
        self.auth.require("automations.manage")

        workflow = Workflow(
            organization_id=self.auth.organization_id,
            name=payload.name,
            description=payload.description,
            is_enabled=False,
            created_by=actor.id,
            updated_by=actor.id,
        )
        self.session.add(workflow)
        await self.session.flush()

        version = WorkflowVersion(
            organization_id=self.auth.organization_id,
            workflow_id=workflow.id,
            version=1,
            status="draft",
            trigger_type=payload.trigger_type,
            definition=payload.definition or {"trigger": {"type": payload.trigger_type}},
            created_by=actor.id,
        )
        self.session.add(version)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=workflow.id,
            metadata={"name": workflow.name, "trigger": payload.trigger_type},
        )
        return workflow, version

    async def save_draft(
        self, workflow_id: UUID, definition: dict[str, Any], actor: User
    ) -> WorkflowVersion:
        """Write the builder's current state.

        **Not validated.** A draft is allowed to be incoherent — that is what a
        draft is — and refusing every save until the graph is complete makes the
        builder unusable halfway through building something.

        Editing a workflow whose latest version is published forks a new draft,
        so a run already in flight keeps executing the definition it started on.
        """
        self.auth.require("automations.manage")
        workflow = await self.get_workflow(workflow_id)

        draft = await self.workflows.latest_draft(
            workflow_id, self.auth.organization_id
        )
        trigger_type = str((definition.get("trigger") or {}).get("type", ""))

        if draft is None:
            highest = await self.workflows.highest_version(
                workflow_id, self.auth.organization_id
            )
            draft = WorkflowVersion(
                organization_id=self.auth.organization_id,
                workflow_id=workflow.id,
                version=highest + 1,
                status="draft",
                trigger_type=trigger_type,
                definition=definition,
                created_by=actor.id,
            )
            self.session.add(draft)
        else:
            draft.definition = definition
            draft.trigger_type = trigger_type

        workflow.updated_by = actor.id
        await self.session.flush()
        return draft

    async def publish(self, workflow_id: UUID, actor: User) -> WorkflowVersion:
        """Validate the draft and make it the live version.

        Validation happens here rather than on save, and it is the only place
        that can refuse. A workflow that cannot be published is a message in the
        builder; one that publishes broken is an incident on live customer data.
        """
        self.auth.require("automations.manage")
        workflow = await self.get_workflow(workflow_id)

        draft = await self.workflows.latest_draft(
            workflow_id, self.auth.organization_id
        )
        if draft is None:
            raise ConflictError("There is nothing to publish.")

        validate_or_raise(draft.definition)

        previous = await self.workflows.published_version(
            workflow_id, self.auth.organization_id
        )
        if previous is not None:
            # Archived, not deleted: a run in flight still points at it, and
            # "what was this doing last Tuesday" needs an answer.
            previous.status = "archived"

        draft.status = "published"
        draft.published_at = datetime.now(UTC)
        workflow.published_version_id = draft.id
        workflow.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.WORKFLOW_PUBLISHED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=workflow.id,
            metadata={"version": draft.version, "trigger": draft.trigger_type},
        )
        logger.info(
            "workflow_published",
            extra={"workflow_id": str(workflow.id), "version": draft.version},
        )
        return draft

    async def set_enabled(
        self, workflow_id: UUID, enabled: bool, actor: User
    ) -> Workflow:
        """The switch. Separate from publishing, so pausing a misbehaving
        automation at 2am does not require editing or republishing anything."""
        self.auth.require("automations.manage")
        workflow = await self.get_workflow(workflow_id)

        if enabled and workflow.published_version_id is None:
            raise ConflictError(
                "Publish this workflow before turning it on."
            )

        workflow.is_enabled = enabled
        workflow.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=(
                AuditAction.WORKFLOW_ENABLED
                if enabled
                else AuditAction.WORKFLOW_DISABLED
            ),
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=workflow.id,
            metadata={"name": workflow.name},
        )
        return workflow

    async def update_workflow(
        self, workflow_id: UUID, payload: WorkflowUpdate, actor: User
    ) -> Workflow:
        self.auth.require("automations.manage")
        workflow = await self.get_workflow(workflow_id)

        updates = payload.model_dump(exclude_unset=True)
        for field, value in updates.items():
            setattr(workflow, field, value)
        workflow.updated_by = actor.id
        await self.session.flush()
        return workflow

    async def delete_workflow(self, workflow_id: UUID, actor: User) -> None:
        """Soft delete, and switch it off on the way out.

        A deleted-but-enabled workflow whose rows survive is exactly the kind of
        thing that keeps firing after somebody thought they had stopped it.
        """
        self.auth.require("automations.manage")
        workflow = await self.get_workflow(workflow_id)

        workflow.is_enabled = False
        workflow.deleted_at = datetime.now(UTC)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=workflow.id,
            metadata={"name": workflow.name},
        )

    def validate_draft(self, definition: dict[str, Any]) -> list[str]:
        """Everything wrong with a definition, for the builder to render.

        Read-only and cheap, so the builder can call it on every change rather
        than making the author discover problems at publish time.
        """
        self.auth.require("automations.view")
        return validate(parse(definition))

    # ----------------------------------------------------------------- runs

    async def list_runs(
        self, *, workflow_id: UUID | None = None, limit: int = 50
    ) -> list[WorkflowRun]:
        self.auth.require("automations.view")
        return await self.runs.list_recent(
            self.auth.organization_id, workflow_id=workflow_id, limit=limit
        )

    async def get_run(self, run_id: UUID) -> WorkflowRun:
        self.auth.require("automations.view")
        run = await self.runs.get_with_steps(run_id, self.auth.organization_id)
        if run is None:
            raise NotFoundError("Run not found.")
        return run

    async def cancel_run(self, run_id: UUID, actor: User) -> WorkflowRun:
        """Stop a run that is parked on a delay.

        Only `waiting` runs can be cancelled: a `running` one is inside a
        worker, and a flag it never checks is not a cancellation.
        """
        self.auth.require("automations.manage")
        run = await self.get_run(run_id)

        if run.status != "waiting":
            raise ConflictError(
                "Only a run that is waiting on a delay can be cancelled."
            )

        run.status = "cancelled"
        run.finished_at = datetime.now(UTC)
        run.resume_at = None
        await self.session.flush()
        logger.info("workflow_run_cancelled", extra={"run_id": str(run.id)})
        return run


class DispatchService:
    """Turns outbox events into runs. Machine-side; no user, no auth context."""

    def __init__(self, session: AsyncSession, organization_id: UUID) -> None:
        self.session = session
        self.organization_id = organization_id
        self.workflows = WorkflowRepository(session)

    async def dispatch(self, event: WorkflowEvent) -> list[WorkflowRun]:
        """Create a run for every enabled workflow that wants this event.

        Marks the event dispatched whatever the outcome, including when nothing
        matched — an event nobody listens for is handled, not pending, and
        leaving it NULL would have the sweep re-examine it forever.
        """
        created: list[WorkflowRun] = []

        if was_caused_by_automation(event.payload):
            # A workflow's own writes must not trigger workflows. Stricter than
            # a depth limit, and the right default when the failure mode is
            # unbounded outbound email. See app/automation/events.py.
            event.dispatched_at = datetime.now(UTC)
            await self.session.flush()
            logger.info(
                "workflow_event_skipped_automation",
                extra={"event_id": str(event.id)},
            )
            return created

        trigger = TRIGGERS.get(event.event_type)
        versions = await self.workflows.published_for_trigger(
            self.organization_id, event.event_type
        )

        for version in versions:
            definition = parse(version.definition)
            if trigger is not None and not trigger_matches(
                trigger, definition.trigger_config, event.payload
            ):
                # Narrowed out before a run exists, so a workflow scoped to one
                # stage does not fill the log with runs it immediately abandons.
                continue

            run = WorkflowRun(
                organization_id=self.organization_id,
                workflow_id=version.workflow_id,
                version_id=version.id,
                event_id=event.id,
                status="pending",
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                context=dict(event.payload),
            )
            self.session.add(run)
            created.append(run)

        event.dispatched_at = datetime.now(UTC)
        await self.session.flush()

        if created:
            logger.info(
                "workflow_event_dispatched",
                extra={"event_id": str(event.id), "runs": len(created)},
            )
        return created


__all__ = ["AutomationService", "DefinitionError", "DispatchService"]
