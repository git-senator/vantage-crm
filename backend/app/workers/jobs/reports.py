"""Report execution, scheduling and export retention.

Three jobs:

  * `run_report_export` — renders one queued run and stores the file;
  * `sweep_scheduled_reports` — enqueues the runs that have come due, and
    re-queues anything a failed enqueue left stranded;
  * `sweep_expired_exports` — deletes objects whose run rows have aged out.

**A scheduled run has no user behind it.** `requested_by` stays NULL rather than
being attributed to the report's author: the author did not press anything at
03:00, and an audit trail that says they did is a lying audit trail. Scope is
still enforced — the run executes under a `system_context` holding the dataset's
view grant at ALL scope, which is the honest statement of what a scheduled
workspace report is, and is exactly why creating one requires `reports.export`
rather than `reports.view`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.models.report import ReportDefinition
from app.reporting.datasets import get_dataset
from app.repositories.report import ReportRepository
from app.services.notification_center import NotificationCenter
from app.services.report import ReportService, retention_cutoff
from app.services.storage import get_object_storage
from app.workers.context import (
    active_organization_ids,
    system_context,
    tenant_scope,
    unscoped_scope,
)
from app.workers.queue import JobName, enqueue
from app.workers.runner import job

logger = get_logger(__name__)

#: Grants a machine-run report holds. Every dataset's view permission, at ALL
#: scope — a scheduled workspace report is a workspace-wide report, and
#: pretending otherwise by picking one author's scope would produce a file whose
#: contents depend on who happened to create it.
_REPORT_GRANTS: tuple[str, ...] = (
    "reports.view",
    "reports.export",
    "leads.view",
    "contacts.view",
    "properties.view",
    "deals.view",
    "tasks.view",
    "activities.view",
)


@job(organization_arg=1, max_tries=3)
async def run_report_export(ctx: dict[str, Any], run_id: str, organization_id: str) -> str:
    """Render one run. Returns its terminal status."""
    organization = UUID(organization_id)

    async with tenant_scope(organization) as session:
        auth = system_context(organization, *_REPORT_GRANTS)
        service = ReportService(session, auth)
        repo = ReportRepository(session)

        run = await repo.get_run(UUID(run_id), organization)
        if run is None:
            # The row is gone. Nothing to do, and nothing to retry — raising
            # here would burn the retry budget on work that cannot exist.
            logger.warning("report_run_missing", extra={"run_id": run_id})
            return "missing"
        if run.status in ("succeeded", "partial", "failed"):
            # Already terminal. A duplicate delivery of the job must not
            # re-render and re-charge storage for a file that exists.
            return run.status

        try:
            await service.execute_run(run)
        except Exception as exc:
            await service.fail_run(run, f"{type(exc).__name__}: {exc}")
            await session.commit()
            # Re-raised so the runner records the failure and retries: a render
            # can fail on a transient storage fault, and the row now says why.
            raise

        status = run.status
        await _notify(session, run, organization)
        await session.commit()

    logger.info(
        "report_export_completed",
        extra={"run_id": run_id, "status": status},
    )
    return status


async def _notify(session: Any, run: Any, organization: UUID) -> None:
    """Tell whoever should know that the file is ready.

    A notification carries the run id, not a signed URL. Links expire in five
    minutes and a notification can sit unread for days, so the notification
    points at the run and the user mints a fresh link when they open it.
    """
    center = NotificationCenter(session)
    recipients: list[UUID] = []

    if run.requested_by is not None:
        recipients.append(run.requested_by)
    elif run.definition_id is not None:
        definition = await session.get(ReportDefinition, run.definition_id)
        if definition is not None:
            recipients = [UUID(r) for r in definition.recipients]

    for recipient in recipients:
        await center.raise_notification(
            organization_id=organization,
            recipient_id=recipient,
            category="system",
            type="report.ready",
            title=f"{run.name} is ready",
            body=(
                f"{run.row_count:,} rows exported as {run.format.upper()}."
                + (f" Truncated from {run.total_rows:,}." if run.status == "partial" else "")
            ),
            entity_type="report",
            entity_id=run.id,
            metadata={"run_id": str(run.id), "format": run.format},
        )


@job(max_tries=2)
async def sweep_scheduled_reports(ctx: dict[str, Any]) -> int:
    """Enqueue every report that has come due, in every tenant.

    Also re-queues stranded `queued` runs. The API's enqueue is best-effort, so
    a queue blip leaves a row nobody is working on; this sweep is what makes
    that late rather than lost.
    """
    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    queued = 0
    for organization in organizations:
        queued += await _sweep_one(organization)

    if queued:
        logger.info("scheduled_reports_queued", extra={"count": queued})
    return queued


async def _sweep_one(organization: UUID) -> int:
    now = datetime.now(UTC)
    queued = 0

    async with tenant_scope(organization) as session:
        auth = system_context(organization, *_REPORT_GRANTS)
        service = ReportService(session, auth)
        repo = ReportRepository(session)

        for definition in await repo.due_for_schedule(organization, now):
            try:
                get_dataset(definition.dataset)
            except KeyError:
                # The dataset was removed from the registry after this report
                # was saved. Skip loudly rather than failing the whole sweep for
                # every other tenant.
                logger.warning(
                    "scheduled_report_unknown_dataset",
                    extra={"report_id": str(definition.id), "dataset": definition.dataset},
                )
                continue

            run = await service.request_export(
                definition_id=definition.id,
                definition=None,
                format=definition.schedule_format,
                actor=None,
                is_scheduled=True,
            )
            # Stamped before the commit, so a crash between the two re-runs the
            # report rather than silently skipping a period.
            definition.last_run_at = now
            await session.commit()

            await enqueue(
                JobName.RUN_REPORT_EXPORT,
                str(run.id),
                str(organization),
                job_id=f"report-export:{run.id}",
            )
            queued += 1

        # Anything left queued from a failed enqueue.
        for run in await repo.list_runs(organization, limit=100):
            if run.status != "queued" or (now - run.started_at).total_seconds() < 300:
                continue
            await enqueue(
                JobName.RUN_REPORT_EXPORT,
                str(run.id),
                str(organization),
                job_id=f"report-export-retry:{run.id}",
            )
            queued += 1

    return queued


@job(max_tries=2)
async def sweep_expired_exports(ctx: dict[str, Any]) -> int:
    """Delete export objects past retention. The rows stay.

    The run row is the record that somebody exported eight thousand clients;
    the file is a convenience with a shelf life. Deleting the row along with the
    object would destroy the evidence to save the storage, which is backwards.
    """
    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    cutoff = retention_cutoff()
    storage = get_object_storage()
    deleted = 0

    for organization in organizations:
        async with tenant_scope(organization) as session:
            repo = ReportRepository(session)
            for run in await repo.expired_runs(organization, cutoff):
                key = run.storage_key
                if key is None:  # pragma: no cover — the query filters these out
                    continue
                try:
                    await storage.delete(key)
                except Exception:
                    logger.warning("export_delete_failed", extra={"key": key}, exc_info=True)
                    continue
                run.storage_key = None
                deleted += 1
            await session.commit()

    if deleted:
        logger.info("expired_exports_deleted", extra={"count": deleted})
    return deleted
