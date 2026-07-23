"""Report business logic: saving, running, exporting, scheduling.

**Rows are always resolved against the runner's scope, never the author's.**
Sharing a report widens who may execute it; it never widens what comes back. An
agent running the sales director's shared "all deals" report gets their own
deals. Any other rule turns a saved report into a privilege-escalation
primitive, and it is the kind that looks like a feature in a demo.

**A preview is bounded and synchronous; an export is queued.** Rendering 50,000
rows into an XLSX takes seconds and tens of megabytes, and doing that inside a
request holds a connection while a browser waits. `run_export` writes a `queued`
row and hands off to the worker, which does the work, puts the file in object
storage and notifies the requester. The preview path exists so the builder feels
immediate — capped at `PREVIEW_LIMIT`, which is a UI concern, not a data one.

**Every export is audited.** `report.exported` with the row count is the record
that answers "who took the client list", and it is written before the file is
handed over rather than after.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.report import SCHEDULES, ReportDefinition, ReportRun
from app.models.user import User
from app.reporting.datasets import DATASETS, get_dataset
from app.reporting.export import CONTENT_TYPES, FORMATS, render
from app.reporting.query import ReportSpec, build_count_query, build_query, resolve_spec
from app.repositories.report import ReportRepository
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext, RbacService
from app.services.storage import get_object_storage
from app.services.storage.base import ObjectStorage
from app.services.storage.keys import organization_prefix, sanitize_filename

logger = get_logger(__name__)

#: What the builder shows while someone is composing. Small enough to render
#: instantly; large enough to see whether the filters are right.
PREVIEW_LIMIT = 100

#: How long an export's download link lives. Short: the link is the only thing
#: standing between the file and anyone who intercepts the notification, and a
#: user who needs it again can ask for another.
DOWNLOAD_URL_TTL_SECONDS = 300

#: A run older than this is not worth keeping a file for. The row stays — it is
#: the audit trail — but the object is a cost with no reader.
EXPORT_RETENTION_DAYS = 30


class ReportService:
    def __init__(
        self,
        session: AsyncSession,
        auth: AuthorizationContext,
        *,
        storage: ObjectStorage | None = None,
    ) -> None:
        self.session = session
        self.auth = auth
        self.repo = ReportRepository(session)
        self.rbac = RbacService(session)
        self.audit = AuditService(session)
        self._storage = storage

    @property
    def storage(self) -> ObjectStorage:
        """Resolved on first use, not in `__init__`.

        Constructing a ReportService must not require a configured bucket — the
        definition and history endpoints never touch storage, and making them
        fail on a storage misconfiguration would be a wider blast radius than
        the feature deserves. Injectable for the same reason AttachmentService
        is: the export tests run against a real in-process bucket.
        """
        if self._storage is None:
            self._storage = get_object_storage()
        return self._storage

    # ---------------------------------------------------------- datasets

    def list_datasets(self) -> list[dict[str, Any]]:
        """The catalogue, filtered to what this caller can actually query.

        A dataset the caller holds no grant on is omitted rather than shown and
        then refused — offering a choice that always fails is worse than not
        offering it.
        """
        self.auth.require("reports.view")
        return [
            {
                "key": dataset.key,
                "label": dataset.label,
                "description": dataset.description,
                "fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "type": f.type,
                        "groupable": f.groupable,
                        "aggregatable": f.aggregatable,
                        "sensitive": f.sensitive,
                    }
                    for f in dataset.fields.values()
                ],
            }
            for dataset in DATASETS.values()
            if self.auth.can(dataset.permission)
        ]

    # ------------------------------------------------------------- scope

    async def _owner_ids(self, permission: str) -> list[UUID] | None:
        """The caller's scope on a dataset, or a refusal.

        Resolved through the same path the list endpoints use — see
        docs/ANALYTICS.md §2 for why reporting has no scope of its own.
        """
        scope = self.auth.scope_for(permission)
        if scope is None:
            raise ConflictError("You do not have access to the records this report is built on.")
        return await self.rbac.owner_ids_for_scope(self.auth, scope)

    # ------------------------------------------------------ definitions

    async def list_definitions(self) -> list[ReportDefinition]:
        self.auth.require("reports.view")
        return await self.repo.list_visible(self.auth.organization_id, self.auth.user_id)

    async def get_definition(self, definition_id: UUID) -> ReportDefinition:
        self.auth.require("reports.view")
        definition = await self.repo.get_visible(
            definition_id, self.auth.organization_id, self.auth.user_id
        )
        if definition is None:
            raise NotFoundError("Report not found.")
        return definition

    async def create_definition(
        self,
        *,
        name: str,
        description: str | None,
        definition: dict[str, Any],
        is_shared: bool,
        schedule: str,
        schedule_format: str,
        recipients: list[UUID],
        actor: User,
    ) -> ReportDefinition:
        self.auth.require("reports.view")

        # Validated before it is stored, so a broken definition cannot be saved
        # and then fail nightly at 03:00 for a month.
        spec = resolve_spec(definition)
        self._assert_schedule(schedule, schedule_format)
        if schedule != "none":
            self.auth.require("reports.export")

        record = ReportDefinition(
            organization_id=self.auth.organization_id,
            owner_id=actor.id,
            name=name,
            description=description,
            dataset=spec.dataset.key,
            definition=definition,
            is_shared=is_shared,
            schedule=schedule,
            schedule_format=schedule_format,
            recipients=[str(r) for r in recipients],
            created_by=actor.id,
            updated_by=actor.id,
        )
        self.session.add(record)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="report",
            entity_id=record.id,
            metadata={"name": name, "dataset": spec.dataset.key},
        )
        return record

    async def update_definition(
        self, definition_id: UUID, changes: dict[str, Any], actor: User
    ) -> ReportDefinition:
        record = await self.get_definition(definition_id)
        self._assert_can_edit(record)

        if "definition" in changes and changes["definition"] is not None:
            spec = resolve_spec(changes["definition"])
            record.dataset = spec.dataset.key
            record.definition = changes["definition"]

        for attribute in ("name", "description", "is_shared"):
            if attribute in changes and changes[attribute] is not None:
                setattr(record, attribute, changes[attribute])

        if "schedule" in changes and changes["schedule"] is not None:
            fmt = changes.get("schedule_format") or record.schedule_format
            self._assert_schedule(changes["schedule"], fmt)
            if changes["schedule"] != "none":
                self.auth.require("reports.export")
            record.schedule = changes["schedule"]
            record.schedule_format = fmt

        if "recipients" in changes and changes["recipients"] is not None:
            record.recipients = [str(r) for r in changes["recipients"]]

        record.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="report",
            entity_id=record.id,
            metadata={"name": record.name},
        )
        return record

    async def delete_definition(self, definition_id: UUID, actor: User) -> None:
        record = await self.get_definition(definition_id)
        self._assert_can_edit(record)

        # Soft delete. The run history references it, and a hard delete would
        # leave that history unable to name what was run.
        record.deleted_at = datetime.now(UTC)
        record.schedule = "none"
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="report",
            entity_id=record.id,
            metadata={"name": record.name},
        )

    def _assert_can_edit(self, record: ReportDefinition) -> None:
        """Authors edit their own reports; `reports.export` edits any.

        A shared report is readable by the workspace and writable by its author.
        Without this, sharing a report hands everyone who can see it the ability
        to rewrite what it means.
        """
        if record.owner_id == self.auth.user_id:
            return
        if self.auth.can("reports.export"):
            return
        raise NotFoundError("Report not found.")

    @staticmethod
    def _assert_schedule(schedule: str, schedule_format: str) -> None:
        if schedule not in SCHEDULES:
            raise ConflictError(f"Unknown schedule: {schedule}")
        if schedule_format not in FORMATS:
            raise ConflictError(f"Unknown format: {schedule_format}")

    # ------------------------------------------------------- execution

    async def preview(
        self, definition: dict[str, Any], *, limit: int = PREVIEW_LIMIT
    ) -> dict[str, Any]:
        """Run a specification and return rows directly. Bounded and unaudited.

        Unaudited deliberately: this is the builder's live feedback loop, it
        runs on every keystroke of a filter value, and an audit log dominated by
        previews is one nobody reads. The *export* is the auditable act — it is
        what produces a file.
        """
        self.auth.require("reports.view")
        spec = resolve_spec({**definition, "limit": min(limit, PREVIEW_LIMIT)})
        rows, total = await self._execute(spec)
        return {
            "headers": spec.headers,
            "rows": [[_jsonable(v) for v in row] for row in rows],
            "row_count": len(rows),
            "total_rows": total,
            "truncated": total > len(rows),
        }

    async def _execute(self, spec: ReportSpec) -> tuple[list[tuple[Any, ...]], int]:
        owner_ids = await self._owner_ids(spec.dataset.permission)
        organization = self.auth.organization_id

        result = await self.session.execute(build_query(spec, organization, owner_ids))
        rows = [tuple(row) for row in result.all()]

        count = await self.session.execute(build_count_query(spec, organization, owner_ids))
        return rows, int(count.scalar() or 0)

    async def request_export(
        self,
        *,
        definition_id: UUID | None,
        definition: dict[str, Any] | None,
        format: str,
        actor: User | None,
        is_scheduled: bool = False,
    ) -> ReportRun:
        """Record the intent to export. The worker does the work.

        Returns immediately with a `queued` run the caller can poll. The
        alternative — rendering inline — holds a request open for however long
        the biggest tenant's biggest report takes.
        """
        self.auth.require("reports.export")
        if format not in FORMATS:
            raise ConflictError(f"Unknown format: {format}")

        raw, name, record = await self._resolve_source(definition_id, definition)
        spec = resolve_spec(raw)

        run = ReportRun(
            organization_id=self.auth.organization_id,
            definition_id=record.id if record is not None else None,
            requested_by=actor.id if actor is not None else None,
            name=name,
            dataset=spec.dataset.key,
            definition_snapshot=raw,
            format=format,
            status="queued",
            is_scheduled=is_scheduled,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def _resolve_source(
        self, definition_id: UUID | None, definition: dict[str, Any] | None
    ) -> tuple[dict[str, Any], str, ReportDefinition | None]:
        if definition_id is not None:
            record = await self.get_definition(definition_id)
            return dict(record.definition), record.name, record
        if definition is None:
            raise ConflictError("An export needs either a saved report or a definition.")
        dataset = get_dataset(str(definition.get("dataset", "")))
        return definition, f"{dataset.label} export", None

    async def execute_run(self, run: ReportRun) -> ReportRun:
        """Do the work. Called by the worker, inside the tenant's scope.

        The run row is the state machine: `running` while it works, then
        `succeeded`, `partial` or `failed`. A crash mid-render leaves `running`,
        which the sweep can reap — better than a row that claims success because
        the status was written optimistically up front.
        """
        run.status = "running"
        await self.session.flush()

        spec = resolve_spec(run.definition_snapshot)
        rows, total = await self._execute(spec)

        payload = render(
            run.format,
            spec.headers,
            rows,
            title=run.name,
            subtitle=f"{spec.dataset.label} · generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        )

        key = export_key(self.auth.organization_id, run.id, run.name, run.format)
        await self.storage.write(key, payload, content_type=CONTENT_TYPES[run.format])

        run.storage_key = key
        run.size_bytes = len(payload)
        run.row_count = len(rows)
        run.total_rows = total
        # `partial`, not `succeeded`, when the cap bit. A truncated export that
        # claims completeness is the failure this status exists to prevent.
        run.status = "partial" if total > len(rows) else "succeeded"
        run.completed_at = datetime.now(UTC)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_EXPORTED,
            organization_id=self.auth.organization_id,
            actor_id=run.requested_by,
            entity_type="report",
            entity_id=run.definition_id or run.id,
            metadata={
                "run_id": str(run.id),
                "dataset": run.dataset,
                "format": run.format,
                "rows": run.row_count,
                "of": run.total_rows,
                "scheduled": run.is_scheduled,
            },
        )
        return run

    async def fail_run(self, run: ReportRun, error: str) -> None:
        run.status = "failed"
        # Truncated: an exception string can carry a query fragment, and a
        # failure record is not a place to accumulate unbounded text.
        run.error = error[:2000]
        run.completed_at = datetime.now(UTC)
        await self.session.flush()

    # ------------------------------------------------------------- runs

    async def list_runs(self, *, definition_id: UUID | None = None) -> list[ReportRun]:
        self.auth.require("reports.view")
        return await self.repo.list_runs(
            self.auth.organization_id,
            definition_id=definition_id,
            # Own runs unless the caller can export — the run history names what
            # people exported, which is closer to an audit record than a report.
            requested_by=None if self.auth.can("reports.export") else self.auth.user_id,
        )

    async def get_run(self, run_id: UUID) -> ReportRun:
        self.auth.require("reports.view")
        run = await self.repo.get_run(run_id, self.auth.organization_id)
        if run is None:
            raise NotFoundError("Report run not found.")
        if run.requested_by != self.auth.user_id and not self.auth.can("reports.export"):
            raise NotFoundError("Report run not found.")
        return run

    async def download_url(self, run_id: UUID) -> str:
        """A short-lived signed link to the finished file."""
        run = await self.get_run(run_id)
        if run.storage_key is None or run.status not in ("succeeded", "partial"):
            raise ConflictError("That export has no file to download.")

        presigned = self.storage.presign_get(
            run.storage_key,
            expires_in=DOWNLOAD_URL_TTL_SECONDS,
            filename=f"{sanitize_filename(run.name)}.{run.format}",
            content_type=CONTENT_TYPES[run.format],
        )
        return presigned.url


def export_key(organization_id: UUID, run_id: UUID, name: str, format: str) -> str:
    """Where an export's bytes live.

    Tenant-first like every other key, so a per-tenant lifecycle rule or
    deletion is a prefix operation. The run id is in the path because two
    exports of the same report must not overwrite each other — an old link
    should 404, not silently serve newer numbers.
    """
    stem = sanitize_filename(name).rsplit(".", 1)[0][:80] or "report"
    return f"{organization_prefix(organization_id)}exports/{run_id}/{stem}.{format}"


def retention_cutoff(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) - timedelta(days=EXPORT_RETENTION_DAYS)


def _jsonable(value: Any) -> Any:
    """Preview values, as JSON. Decimals become strings.

    Same rule as analytics: money that has been through a JSON float has lost
    the precision NUMERIC exists to protect.
    """
    from datetime import date
    from decimal import Decimal

    if isinstance(value, Decimal | UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


__all__ = [
    "DOWNLOAD_URL_TTL_SECONDS",
    "EXPORT_RETENTION_DAYS",
    "PREVIEW_LIMIT",
    "ReportService",
    "export_key",
    "retention_cutoff",
]
