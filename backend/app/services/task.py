"""Task business logic.

Follows the LeadService pattern with `assignee_id` as the scope anchor. Three
things are specific to tasks:

**Completing is a domain action.** `POST /tasks/{id}/complete` sets the
timestamp, writes an activity onto the linked record and audits. A PATCH to
`status='done'` is refused, because it would skip all of that and leave
`ck_tasks_completed_at` unsatisfiable.

**Tasks write to the timeline of the record they hang off.** Creating,
completing and reassigning a task linked to a deal all appear on that deal's
timeline — that is the point of a unified timeline, and it is why the
polymorphic pair matches the one `activities` uses exactly.

**Assignment fires a notification hook.** Delivery itself is queued work and
belongs to Phase 3; what exists here is the seam, called at the right moment
inside the transaction boundary, so wiring a real transport later is a change
in one place. See `_notify_assignment`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.emitter import is_automation_actor, record_event
from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.permissions import Scope
from app.models.task import Task
from app.models.user import User
from app.repositories.task import TaskRepository
from app.repositories.user import UserRepository
from app.schemas.common import Cursor
from app.schemas.task import TaskCreate, TaskFilters, TaskUpdate
from app.services.activity import ActivityService
from app.services.audit import AuditService, build_diff
from app.services.entity_access import EntityAccess
from app.services.notification_center import NotificationCenter
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

ENTITY_TYPE = "task"

AUDITED_FIELDS = (
    "title",
    "description",
    "status",
    "priority",
    "due_at",
    "assignee_id",
    "entity_type",
    "entity_id",
)


class TaskService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.tasks = TaskRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditService(session)
        self.activities = ActivityService(session)
        self.access = EntityAccess(session, auth)
        self.rbac = RbacService(session)

    # -------------------------------------------------------------- scope

    async def _assignee_ids(self, permission: str) -> list[UUID] | None:
        scope = self.auth.require(permission)
        return await self.rbac.owner_ids_for_scope(self.auth, scope)

    # --------------------------------------------------------------- read

    async def list_tasks(
        self, *, filters: TaskFilters, limit: int, cursor: Cursor | None
    ) -> tuple[list[Task], bool]:
        assignee_ids = await self._assignee_ids("tasks.view")
        if filters.entity_id is not None and filters.entity_type is not None:
            # Listing a record's tasks requires being able to read the record,
            # exactly as the activity timeline does.
            await self.access.assert_readable(filters.entity_type, filters.entity_id)
        return await self.tasks.list_page(
            self.auth.organization_id,
            assignee_ids=assignee_ids,
            filters=filters,
            limit=limit,
            cursor=cursor,
        )

    async def queue(self, *, filters: TaskFilters, limit: int = 25) -> list[Task]:
        """The work queue: soonest due first, undated last."""
        assignee_ids = await self._assignee_ids("tasks.view")
        return await self.tasks.list_queue(
            self.auth.organization_id,
            assignee_ids=assignee_ids,
            filters=filters,
            limit=limit,
        )

    async def get_task(self, task_id: UUID) -> Task:
        assignee_ids = await self._assignee_ids("tasks.view")
        task = await self.tasks.get_visible(
            task_id, self.auth.organization_id, assignee_ids
        )
        if task is None:
            raise NotFoundError("Task not found.")
        return task

    async def status_counts(self) -> dict[str, int]:
        assignee_ids = await self._assignee_ids("tasks.view")
        return await self.tasks.count_by_status(
            self.auth.organization_id, assignee_ids
        )

    # -------------------------------------------------------------- write

    async def create_task(self, payload: TaskCreate, actor: User) -> Task:
        self.auth.require("tasks.manage")

        assignee_id = payload.assignee_id or actor.id
        if assignee_id != actor.id:
            await self._assert_can_assign_to(assignee_id)

        if payload.entity_type is not None and payload.entity_id is not None:
            # You cannot attach work to a record you cannot see.
            await self.access.assert_readable(payload.entity_type, payload.entity_id)

        task = Task(
            organization_id=self.auth.organization_id,
            assignee_id=assignee_id,
            created_by=actor.id,
            updated_by=actor.id,
            **payload.model_dump(exclude={"assignee_id"}),
        )
        self.session.add(task)
        await self.session.flush()

        await self._log_on_entity(
            task,
            subject=f"Task created: {task.title}",
            body=task.description,
            actor=actor,
        )
        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=task.id,
            metadata={"priority": task.priority, "due_at": task.due_at},
        )
        if assignee_id != actor.id:
            await self._notify_assignment(task, assignee_id, actor)

        await record_event(
            self.session,
            organization_id=self.auth.organization_id,
            event_type="task.created",
            entity_type="task",
            record=task,
            actor_id=actor.id,
            by_automation=is_automation_actor(self.auth.role_keys),
        )
        logger.info("task_created", extra={"task_id": str(task.id)})
        return await self.get_task(task.id)

    async def update_task(
        self, task_id: UUID, payload: TaskUpdate, actor: User
    ) -> Task:
        assignee_ids = await self._assignee_ids("tasks.manage")
        task = await self.tasks.get_visible(
            task_id, self.auth.organization_id, assignee_ids
        )
        if task is None:
            raise NotFoundError("Task not found.")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            return task

        # Completing has side effects — a timestamp, an activity, an audit
        # entry — and a PATCH cannot be allowed to skip them. It would also
        # violate ck_tasks_completed_at.
        if updates.get("status") == "done":
            raise ConflictError(
                "Use the complete endpoint to finish a task."
            )
        # Reopening through a PATCH is fine, but the timestamp has to follow.
        if (
            "status" in updates
            and updates["status"] != "done"
            and task.status == "done"
        ):
            task.completed_at = None

        if "assignee_id" in updates and updates["assignee_id"] != task.assignee_id:
            await self._assert_can_assign_to(updates["assignee_id"])

        merged_type = updates.get("entity_type", task.entity_type)
        merged_id = updates.get("entity_id", task.entity_id)
        if (merged_type is None) != (merged_id is None):
            raise ConflictError(
                "entity_type and entity_id must be set together, or both cleared."
            )
        if merged_type is not None and merged_id is not None and (
            "entity_type" in updates or "entity_id" in updates
        ):
            await self.access.assert_readable(merged_type, merged_id)

        previous_assignee = task.assignee_id
        before = {field: getattr(task, field) for field in AUDITED_FIELDS}
        for field, value in updates.items():
            setattr(task, field, value)
        task.updated_by = actor.id
        await self.session.flush()

        diff = build_diff(
            before, {field: getattr(task, field) for field in AUDITED_FIELDS}
        )
        if diff:
            await self.audit.record(
                action=AuditAction.RECORD_UPDATED,
                organization_id=self.auth.organization_id,
                actor_id=actor.id,
                actor_email=actor.email,
                entity_type=ENTITY_TYPE,
                entity_id=task.id,
                metadata={"changes": diff},
            )
        if task.assignee_id and task.assignee_id != previous_assignee:
            await self._notify_assignment(task, task.assignee_id, actor)

        return await self.get_task(task.id)

    async def complete_task(
        self, task_id: UUID, actor: User, note: str | None = None
    ) -> Task:
        """Finish a task.

        Sets `completed_at` alongside `status`, so the pair stays coherent and
        "how long did this take" remains answerable. Writes the completion onto
        the linked record's timeline, which is the whole reason a task carries
        an entity link.
        """
        assignee_ids = await self._assignee_ids("tasks.manage")
        task = await self.tasks.get_visible(
            task_id, self.auth.organization_id, assignee_ids
        )
        if task is None:
            raise NotFoundError("Task not found.")
        if task.is_done:
            raise ConflictError("This task is already complete.")

        task.status = "done"
        task.completed_at = datetime.now(UTC)
        task.updated_by = actor.id
        await self.session.flush()

        await self._log_on_entity(
            task,
            subject=f"Task completed: {task.title}",
            body=note,
            actor=actor,
        )
        await self.audit.record(
            action=AuditAction.RECORD_COMPLETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=task.id,
            metadata={"title": task.title},
        )
        await record_event(
            self.session,
            organization_id=self.auth.organization_id,
            event_type="task.completed",
            entity_type="task",
            record=task,
            actor_id=actor.id,
            by_automation=is_automation_actor(self.auth.role_keys),
        )
        logger.info("task_completed", extra={"task_id": str(task.id)})
        return await self.get_task(task.id)

    async def reopen_task(self, task_id: UUID, actor: User) -> Task:
        """Undo a completion. Clears the timestamp so the pair stays coherent."""
        assignee_ids = await self._assignee_ids("tasks.manage")
        task = await self.tasks.get_visible(
            task_id, self.auth.organization_id, assignee_ids
        )
        if task is None:
            raise NotFoundError("Task not found.")
        if not task.is_done:
            raise ConflictError("This task is not complete.")

        task.status = "todo"
        task.completed_at = None
        task.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=task.id,
            metadata={"reopened": True},
        )
        return await self.get_task(task.id)

    async def delete_task(self, task_id: UUID, actor: User) -> None:
        """Soft delete. The audit trail survives."""
        assignee_ids = await self._assignee_ids("tasks.manage")
        task = await self.tasks.get_visible(
            task_id, self.auth.organization_id, assignee_ids
        )
        if task is None:
            raise NotFoundError("Task not found.")

        task.deleted_at = datetime.now(UTC)
        task.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=task.id,
            metadata={"title": task.title},
        )

    async def assign_task(self, task_id: UUID, assignee_id: UUID, actor: User) -> Task:
        self.auth.require("tasks.manage")
        await self._assert_can_assign_to(assignee_id)

        assignee_ids = await self._assignee_ids("tasks.manage")
        task = await self.tasks.get_visible(
            task_id, self.auth.organization_id, assignee_ids
        )
        if task is None:
            raise NotFoundError("Task not found.")

        previous = task.assignee_id
        task.assignee_id = assignee_id
        task.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=task.id,
            metadata={
                "changes": {
                    "assignee_id": {
                        "old": str(previous) if previous else None,
                        "new": str(assignee_id),
                    }
                }
            },
        )
        await self._notify_assignment(task, assignee_id, actor)
        return await self.get_task(task.id)

    # ------------------------------------------------------------ helpers

    async def _log_on_entity(
        self, task: Task, *, subject: str, body: str | None, actor: User
    ) -> None:
        """Write a task event onto the linked record's timeline.

        Silent no-op for a standalone task — "call the title company back" is a
        real task with no record attached, and there is nowhere to log it.
        """
        if task.entity_type is None or task.entity_id is None:
            return

        await self.activities.record(
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            entity_type=task.entity_type,
            entity_id=task.entity_id,
            type="note",
            subject=subject,
            body=body,
            metadata={"task_id": str(task.id), "source": "task"},
        )

    async def _notify_assignment(
        self, task: Task, assignee_id: UUID, actor: User
    ) -> None:
        """Raise "a task was assigned to you".

        Phase 2.7 left this as a seam that only logged. Phase 3.2 wired it to a
        dedicated email job. Phase 3.3 replaced that with the notification
        centre, which is the version that scales: this service says *what
        happened and to whom*, and the centre decides — from the recipient's own
        preferences — whether that also becomes an email. A service that reached
        for the mail queue directly would be a service that has to be edited
        every time somebody adds a delivery channel.

        Self-assignment is skipped — nobody needs telling about their own work.
        """
        if assignee_id == actor.id:
            return

        assignee = await self.users.get(assignee_id, self.auth.organization_id)
        if assignee is None:  # pragma: no cover - guarded by _assert_can_assign_to
            return

        await record_event(
            self.session,
            organization_id=self.auth.organization_id,
            event_type="task.assigned",
            entity_type="task",
            record=task,
            actor_id=actor.id,
            extra={"assignee_id": str(assignee_id)},
            by_automation=is_automation_actor(self.auth.role_keys),
        )

        due = f" Due {task.due_at:%d %b %Y}." if task.due_at else ""
        await NotificationCenter(self.session).raise_notification(
            organization_id=self.auth.organization_id,
            recipient_id=assignee_id,
            actor_id=actor.id,
            category="task",
            type="task.assigned",
            title=f"{actor.full_name} assigned you a task",
            body=f"{task.title}.{due}",
            entity_type="task",
            entity_id=task.id,
        )

    async def _assert_can_assign_to(self, assignee_id: UUID) -> None:
        """The assignee must be a real member of this workspace.

        Without this an id from another tenant could be written into
        `assignee_id`. RLS would then hide the task from everyone, because no
        user in this organization matches — work silently lost rather than
        leaked, but lost all the same.
        """
        target = await self.users.get(assignee_id, self.auth.organization_id)
        if target is None:
            raise NotFoundError("Assignee is not a member of this workspace.")

        scope = self.auth.scope_for("tasks.manage")
        if scope is None:
            raise PermissionDeniedError(
                "This action requires the 'tasks.manage' permission."
            )
        if scope is Scope.OWN and assignee_id != self.auth.user_id:
            raise PermissionDeniedError("You can only assign tasks to yourself.")
        if scope is Scope.TEAM:
            teammates = await self.rbac.team_member_ids(
                self.auth.user_id, self.auth.organization_id
            )
            if assignee_id not in teammates:
                raise PermissionDeniedError(
                    "You can only assign tasks within your team."
                )
