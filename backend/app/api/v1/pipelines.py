"""Pipeline endpoints.

Reads are gated on `deals.view` — every agent needs the stages to render a
board. Writes are gated on `settings.manage`, because the pipeline is workspace
configuration and an agent must not be able to delete the stage their
colleague's deals sit in.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    TenantSessionDep,
    require,
    verify_csrf,
)
from app.schemas.pipeline import (
    PipelineCreate,
    PipelineRead,
    PipelineStageCreate,
    PipelineStageUpdate,
    PipelineUpdate,
)
from app.services.pipeline import PipelineService

router = APIRouter()


def _to_read(pipeline) -> PipelineRead:  # type: ignore[no-untyped-def]
    return PipelineRead(
        id=pipeline.id,
        name=pipeline.name,
        description=pipeline.description,
        is_default=pipeline.is_default,
        stages=[
            {
                "id": stage.id,
                "key": stage.key,
                "name": stage.name,
                "position": stage.position,
                "default_probability": stage.default_probability,
                "is_won": stage.is_won,
                "is_lost": stage.is_lost,
                "is_terminal": stage.is_terminal,
            }
            for stage in sorted(pipeline.stages, key=lambda s: (s.position, s.key))
        ],
        created_at=pipeline.created_at,
        updated_at=pipeline.updated_at,
    )


@router.get("", response_model=list[PipelineRead])
async def list_pipelines(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("deals.view"))],
    _user: CurrentUser,
) -> list[PipelineRead]:
    """Every pipeline in the workspace, default first.

    Not paginated: a workspace has a handful of pipelines, and the board needs
    all of them to populate its switcher.
    """
    rows = await PipelineService(session, auth).list_pipelines()
    return [_to_read(row) for row in rows]


@router.get("/{pipeline_id}", response_model=PipelineRead)
async def get_pipeline(
    pipeline_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("deals.view"))],
    _user: CurrentUser,
) -> PipelineRead:
    return _to_read(await PipelineService(session, auth).get_pipeline(pipeline_id))


@router.post(
    "",
    response_model=PipelineRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_pipeline(
    payload: PipelineCreate,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("settings.manage"))],
    user: CurrentUser,
) -> PipelineRead:
    pipeline = await PipelineService(session, auth).create_pipeline(payload, user)
    response.headers["Location"] = f"/api/v1/pipelines/{pipeline.id}"
    return _to_read(pipeline)


@router.patch(
    "/{pipeline_id}",
    response_model=PipelineRead,
    dependencies=[Depends(verify_csrf)],
)
async def update_pipeline(
    pipeline_id: UUID,
    payload: PipelineUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("settings.manage"))],
    user: CurrentUser,
) -> PipelineRead:
    """Metadata only. Stages have their own endpoints."""
    return _to_read(
        await PipelineService(session, auth).update_pipeline(
            pipeline_id, payload, user
        )
    )


@router.delete(
    "/{pipeline_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_pipeline(
    pipeline_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("settings.manage"))],
    user: CurrentUser,
) -> None:
    """Soft delete. 409 while the pipeline still holds deals, or is default."""
    await PipelineService(session, auth).delete_pipeline(pipeline_id, user)


@router.post(
    "/{pipeline_id}/stages",
    response_model=PipelineRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def add_stage(
    pipeline_id: UUID,
    payload: PipelineStageCreate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("settings.manage"))],
    user: CurrentUser,
) -> PipelineRead:
    return _to_read(
        await PipelineService(session, auth).add_stage(pipeline_id, payload, user)
    )


@router.patch(
    "/{pipeline_id}/stages/{stage_id}",
    response_model=PipelineRead,
    dependencies=[Depends(verify_csrf)],
)
async def update_stage(
    pipeline_id: UUID,
    stage_id: UUID,
    payload: PipelineStageUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("settings.manage"))],
    user: CurrentUser,
) -> PipelineRead:
    """Rename, reorder, or change a stage's outcome flags."""
    return _to_read(
        await PipelineService(session, auth).update_stage(
            pipeline_id, stage_id, payload, user
        )
    )


@router.delete(
    "/{pipeline_id}/stages/{stage_id}",
    response_model=PipelineRead,
    dependencies=[Depends(verify_csrf)],
)
async def delete_stage(
    pipeline_id: UUID,
    stage_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("settings.manage"))],
    user: CurrentUser,
) -> PipelineRead:
    """Remove a stage. 409 while it still holds deals, or if it is the last."""
    return _to_read(
        await PipelineService(session, auth).delete_stage(pipeline_id, stage_id, user)
    )
