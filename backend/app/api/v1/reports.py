"""Report endpoints: datasets, saved reports, preview, export, history."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.v1.dependencies import Authorization, CurrentUser, TenantSessionDep
from app.core.logging import get_logger
from app.schemas.report import (
    DatasetRead,
    DownloadResponse,
    ExportRequest,
    ReportDefinitionCreate,
    ReportDefinitionRead,
    ReportDefinitionUpdate,
    ReportPreview,
    ReportRunRead,
    ReportSpecIn,
)
from app.services.report import DOWNLOAD_URL_TTL_SECONDS, ReportService
from app.workers.queue import JobName, enqueue

logger = get_logger(__name__)
router = APIRouter()


@router.get("/datasets", response_model=list[DatasetRead])
async def list_datasets(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[DatasetRead]:
    """What can be reported on, filtered to what this caller may query."""
    return [DatasetRead.model_validate(row) for row in ReportService(session, auth).list_datasets()]


@router.post("/preview", response_model=ReportPreview)
async def preview(
    payload: ReportSpecIn,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> ReportPreview:
    """Run a specification and return rows inline. Bounded; not audited.

    The builder's feedback loop — it fires on every filter change, and an audit
    log dominated by previews is one nobody reads. Producing a *file* is the
    auditable act.
    """
    result = await ReportService(session, auth).preview(payload.model_dump(mode="json"))
    return ReportPreview.model_validate(result)


@router.get("", response_model=list[ReportDefinitionRead])
async def list_reports(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[ReportDefinitionRead]:
    rows = await ReportService(session, auth).list_definitions()
    return [ReportDefinitionRead.model_validate(row) for row in rows]


@router.post("", response_model=ReportDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportDefinitionCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ReportDefinitionRead:
    record = await ReportService(session, auth).create_definition(
        name=payload.name,
        description=payload.description,
        definition=payload.definition.model_dump(mode="json"),
        is_shared=payload.is_shared,
        schedule=payload.schedule,
        schedule_format=payload.schedule_format,
        recipients=payload.recipients,
        actor=user,
    )
    await session.commit()
    return ReportDefinitionRead.model_validate(record)


# ---------------------------------------------------------------------------
# Literal single-segment routes must stay above `/{definition_id}`.
#
# FastAPI matches in declaration order, and a path parameter matches one segment
# — `[^/]+`. So `/reports/runs/history` is safe wherever it sits (two segments
# cannot bind to one parameter), but `/reports/datasets` is **not**: with
# `/{definition_id}` first it binds `definition_id="datasets"` and fails to
# parse as a UUID, producing a 422 on a route that plainly exists.
#
# The runs routes are grouped here for readability; `/datasets` above is the one
# whose position is load-bearing, and there is a test pinning it.
# ---------------------------------------------------------------------------

@router.get("/runs/history", response_model=list[ReportRunRead])
async def list_runs(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    definition_id: UUID | None = None,
) -> list[ReportRunRead]:
    rows = await ReportService(session, auth).list_runs(definition_id=definition_id)
    return [ReportRunRead.model_validate(row) for row in rows]


@router.get("/runs/{run_id}", response_model=ReportRunRead)
async def get_run(
    run_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> ReportRunRead:
    run = await ReportService(session, auth).get_run(run_id)
    return ReportRunRead.model_validate(run)


@router.get("/runs/{run_id}/download", response_model=DownloadResponse)
async def download_run(
    run_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> DownloadResponse:
    """A short-lived signed link. The bytes never pass through this process."""
    url = await ReportService(session, auth).download_url(run_id)
    return DownloadResponse(url=url, expires_in=DOWNLOAD_URL_TTL_SECONDS)


@router.get("/{definition_id}", response_model=ReportDefinitionRead)
async def get_report(
    definition_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> ReportDefinitionRead:
    record = await ReportService(session, auth).get_definition(definition_id)
    return ReportDefinitionRead.model_validate(record)


@router.patch("/{definition_id}", response_model=ReportDefinitionRead)
async def update_report(
    definition_id: UUID,
    payload: ReportDefinitionUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ReportDefinitionRead:
    changes = payload.model_dump(mode="json", exclude_unset=True)
    record = await ReportService(session, auth).update_definition(definition_id, changes, user)
    await session.commit()
    return ReportDefinitionRead.model_validate(record)


@router.delete("/{definition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    definition_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await ReportService(session, auth).delete_definition(definition_id, user)
    await session.commit()


@router.post("/export", response_model=ReportRunRead, status_code=status.HTTP_202_ACCEPTED)
async def export_report(
    payload: ExportRequest,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ReportRunRead:
    """Queue an export. 202, because the file does not exist yet.

    The run row is written and committed *before* the job is enqueued: a worker
    that picks the job up in the same millisecond must find the row it is being
    told to process. Enqueue-then-commit is how a job arrives for a row that
    does not exist yet, and it fails intermittently under exactly the load that
    makes it hardest to reproduce.
    """
    run = await ReportService(session, auth).request_export(
        definition_id=payload.definition_id,
        definition=(
            payload.definition.model_dump(mode="json") if payload.definition is not None else None
        ),
        format=payload.format,
        actor=user,
    )
    await session.commit()

    # Best-effort. A failed enqueue leaves a `queued` row the sweep will pick
    # up, so the export is late rather than lost.
    await enqueue(
        JobName.RUN_REPORT_EXPORT,
        str(run.id),
        str(auth.organization_id),
        job_id=f"report-export:{run.id}",
    )
    return ReportRunRead.model_validate(run)
