"""Automation jobs: dispatch events, execute runs, resume delays.

Four jobs and the same belt-and-braces shape the rest of the worker uses. The
fast path is an optimistic enqueue; the sweeps re-find anything that enqueue
lost. What makes that safe here is that **the database is the record of
outstanding work**: an event exists the moment its change commits, and a run
exists the moment its event is dispatched, so a lost job costs latency rather
than an automation that silently never happened.

Execution is sliced. `execute_workflow_run` runs until the workflow ends or hits
a delay, then returns — a parked run holds no worker and no timer, and
`sweep_workflow_runs` wakes it when its `resume_at` passes. That is the only
shape that survives a workflow saying "wait three days".

Every job binds a tenant before touching data, for the reason established in
Phase 3.2: RLS is FORCEd, so an unscoped sweep runs cleanly and touches nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.automation.context import ExecutionContext
from app.automation.definition import parse
from app.automation.executor import WorkflowExecutor
from app.core.logging import get_logger
from app.models.automation import WorkflowRun, WorkflowVersion
from app.models.user import User
from app.repositories.automation import (
    WorkflowEventRepository,
    WorkflowRepository,
    WorkflowRunRepository,
)
from app.services.automation import DispatchService
from app.workers.context import (
    active_organization_ids,
    tenant_scope,
    unscoped_scope,
)
from app.workers.queue import JobName, enqueue
from app.workers.runner import job

logger = get_logger(__name__)


async def _enqueue_runs(run_ids: list[UUID], organization_id: UUID) -> None:
    """Kick off freshly created runs. Best-effort; the sweep is the net.

    Enqueued outside the transaction that created them, so a job cannot start
    before the row it needs is visible to another connection.
    """
    for run_id in run_ids:
        await enqueue(
            JobName.EXECUTE_WORKFLOW_RUN,
            str(run_id),
            str(organization_id),
            job_id=f"wfrun:{run_id}",
        )


@job(organization_arg=1)
async def dispatch_workflow_event(
    ctx: dict[str, Any], event_id: str, organization_id: str
) -> str:
    """Match one outbox event against published workflows.

    Idempotent through `dispatched_at`: a duplicate delivery finds the event
    already handled and does nothing, which matters because the fast path and
    the sweep can both reach the same event.
    """
    organization = UUID(organization_id)

    async with tenant_scope(organization) as session:
        event = await WorkflowEventRepository(session).get_for_dispatch(
            UUID(event_id), organization
        )
        if event is None:
            return "gone"
        if event.dispatched_at is not None:
            return "already_dispatched"

        runs = await DispatchService(session, organization).dispatch(event)
        run_ids = [run.id for run in runs]

    await _enqueue_runs(run_ids, organization)
    return f"dispatched:{len(run_ids)}"


@job(organization_arg=1, max_tries=3)
async def execute_workflow_run(
    ctx: dict[str, Any], run_id: str, organization_id: str
) -> str:
    """Run one execution slice: until the workflow ends, fails, or waits.

    Retries are bounded at three rather than the default five. A workflow step
    that failed twice on a transient fault is unlikely to succeed on the fifth
    attempt, and every attempt may re-contact a customer — the cost of one more
    try is not symmetric with the cost of one more email.
    """
    organization = UUID(organization_id)

    async with tenant_scope(organization) as session:
        run = (
            (
                await session.execute(
                    select(WorkflowRun)
                    .where(WorkflowRun.id == UUID(run_id))
                    .where(WorkflowRun.organization_id == organization)
                )
            )
            .unique()
            .scalar_one_or_none()
        )
        if run is None:
            return "gone"
        if run.status in ("succeeded", "failed", "cancelled"):
            # A duplicate delivery, or a run cancelled while queued. Never
            # re-run a finished workflow: its actions are not idempotent.
            return run.status

        version = (
            (
                await session.execute(
                    select(WorkflowVersion).where(
                        WorkflowVersion.id == run.version_id
                    )
                )
            )
            .unique()
            .scalar_one_or_none()
        )
        if version is None:  # pragma: no cover — cascade keeps these together
            run.status = "failed"
            run.error = "This workflow version no longer exists."
            run.finished_at = datetime.now(UTC)
            return "failed"

        acting_user = await _acting_user(session, run.workflow_id, organization)
        if acting_user is None:
            # Services need an actor for the audit trail, and the honest answer
            # to "who did this" is the workflow's author. With no author left,
            # the run fails visibly rather than acting under a fabricated
            # identity that an audit reader would have to decode later.
            run.status = "failed"
            run.error = (
                "This workflow's author no longer has an account. Reassign or "
                "recreate the workflow to run it."
            )
            run.finished_at = datetime.now(UTC)
            logger.warning(
                "workflow_run_without_author", extra={"run_id": run_id}
            )
            return "failed"

        run.status = "running"
        run.attempts += 1
        run.started_at = run.started_at or datetime.now(UTC)
        await session.flush()

        context = ExecutionContext.for_run(
            organization_id=organization,
            workflow_id=run.workflow_id,
            run_id=run.id,
            acting_user=acting_user,
            entity_type=run.entity_type,
            entity_id=run.entity_id,
            variables=dict(run.context or {}),
        )

        result = await WorkflowExecutor(session).execute(
            run, parse(version.definition), context
        )

        run.context = context.variables
        run.status = result.status
        run.error = result.error
        run.resume_at = result.resume_at
        if result.status in ("succeeded", "failed"):
            run.finished_at = datetime.now(UTC)
            run.resume_at = None

        logger.info(
            "workflow_run_slice_finished",
            extra={
                "run_id": run_id,
                "status": result.status,
                "workflow_id": str(run.workflow_id),
            },
        )
        return result.status


async def _acting_user(
    session: Any, workflow_id: UUID, organization_id: UUID
) -> User | None:
    workflow = await WorkflowRepository(session).get(workflow_id, organization_id)
    if workflow is None or workflow.created_by is None:
        return None
    result = await session.execute(
        select(User)
        .where(User.id == workflow.created_by)
        .where(User.organization_id == organization_id)
    )
    user: User | None = result.unique().scalar_one_or_none()
    return user


@job(max_tries=2)
async def sweep_workflow_events(ctx: dict[str, Any]) -> int:
    """Dispatch outbox events the fast path missed. Returns how many.

    The safety net that lets `emit`'s enqueue be best-effort: the event row is
    already committed, so a lost job is a delay rather than an automation that
    never fires.
    """
    dispatched = 0

    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    for organization in organizations:
        async with tenant_scope(organization) as session:
            events = await WorkflowEventRepository(session).list_undispatched(
                organization
            )
            service = DispatchService(session, organization)
            created: list[UUID] = []
            for event in events:
                runs = await service.dispatch(event)
                created.extend(run.id for run in runs)
                dispatched += 1

        await _enqueue_runs(created, organization)

    if dispatched:
        logger.info("workflow_events_swept", extra={"count": dispatched})
    return dispatched


@job(max_tries=2)
async def sweep_workflow_runs(ctx: dict[str, Any]) -> int:
    """Wake parked runs and restart pending ones. Returns how many.

    Two jobs' worth of work in one sweep because they share the tenant loop and
    the enqueue, and splitting them would double the per-tick cost of a query
    that is normally empty.
    """
    resumed = 0
    now = datetime.now(UTC)

    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    for organization in organizations:
        async with tenant_scope(organization) as session:
            repository = WorkflowRunRepository(session)
            waiting = await repository.list_resumable(organization, now=now)
            pending = await repository.list_pending(organization)
            run_ids = [run.id for run in (*waiting, *pending)]

        for run_id in run_ids:
            # No `job_id` dedupe key here: a resumed run legitimately executes
            # more than once over its life, and reusing the key would make the
            # second slice a silent no-op.
            if await enqueue(
                JobName.EXECUTE_WORKFLOW_RUN, str(run_id), str(organization)
            ):
                resumed += 1

    if resumed:
        logger.info("workflow_runs_swept", extra={"count": resumed})
    return resumed
