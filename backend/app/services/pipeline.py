"""Pipeline configuration.

Reads are gated on `deals.view` — the board cannot render without stages.
Writes are gated on `settings.manage`, because a pipeline is workspace
configuration: an agent who can edit deals must not be able to delete the stage
their colleague's deals sit in.

The interesting rules are all about not breaking live deals. Retiring a stage
that still holds them, or leaving a pipeline with no stages, are both refused
with an explanation rather than allowed to fail as a foreign-key error.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.pipeline import (
    DEFAULT_PIPELINE_NAME,
    DEFAULT_STAGES,
    Pipeline,
    PipelineStage,
)
from app.models.user import User
from app.repositories.deal import DealRepository
from app.repositories.pipeline import PipelineRepository
from app.schemas.pipeline import (
    PipelineCreate,
    PipelineStageCreate,
    PipelineStageUpdate,
    PipelineUpdate,
)
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext

logger = get_logger(__name__)

ENTITY_TYPE = "pipeline"


def build_default_pipeline(organization_id: UUID) -> Pipeline:
    """The pipeline a new workspace starts with.

    Shared by the migration that backfills existing tenants and by workspace
    creation, so the two cannot drift — the same reasoning that puts RLS policy
    DDL in `app/db/sql_objects.py`.
    """
    pipeline = Pipeline(
        organization_id=organization_id,
        name=DEFAULT_PIPELINE_NAME,
        description="The default sales process for this workspace.",
        is_default=True,
    )
    for position, spec in enumerate(DEFAULT_STAGES):
        pipeline.stages.append(
            PipelineStage(
                organization_id=organization_id,
                key=str(spec["key"]),
                name=str(spec["name"]),
                position=position,
                default_probability=int(spec["probability"]),  # type: ignore[call-overload]
                is_won=bool(spec.get("is_won", False)),
                is_lost=bool(spec.get("is_lost", False)),
            )
        )
    return pipeline


class PipelineService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.pipelines = PipelineRepository(session)
        self.deals = DealRepository(session)
        self.audit = AuditService(session)

    # --------------------------------------------------------------- read

    async def list_pipelines(self) -> list[Pipeline]:
        self.auth.require("deals.view")
        return await self.pipelines.list_all(self.auth.organization_id)

    async def get_pipeline(self, pipeline_id: UUID) -> Pipeline:
        self.auth.require("deals.view")
        pipeline = await self.pipelines.get_with_stages(
            pipeline_id, self.auth.organization_id
        )
        if pipeline is None:
            raise NotFoundError("Pipeline not found.")
        return pipeline

    async def resolve_default(self) -> Pipeline:
        """The pipeline a new deal lands in when none is given."""
        pipeline = await self.pipelines.get_default(self.auth.organization_id)
        if pipeline is None:
            raise ConflictError(
                "This workspace has no pipeline configured. "
                "An administrator must create one before deals can be added."
            )
        return pipeline

    # -------------------------------------------------------------- write

    async def create_pipeline(self, payload: PipelineCreate, actor: User) -> Pipeline:
        self.auth.require("settings.manage")

        pipeline = Pipeline(
            organization_id=self.auth.organization_id,
            name=payload.name,
            description=payload.description,
            is_default=payload.is_default,
            created_by=actor.id,
            updated_by=actor.id,
        )
        for spec in payload.stages:
            pipeline.stages.append(
                PipelineStage(
                    organization_id=self.auth.organization_id,
                    key=spec.key,
                    name=spec.name,
                    position=spec.position,
                    default_probability=spec.default_probability,
                    is_won=spec.is_won,
                    is_lost=spec.is_lost,
                )
            )

        if payload.is_default:
            await self._demote_existing_default()

        self.session.add(pipeline)
        await self._flush_translating_conflicts()

        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=pipeline.id,
            metadata={"name": pipeline.name, "stages": len(payload.stages)},
        )
        logger.info("pipeline_created", extra={"pipeline_id": str(pipeline.id)})
        return await self.get_pipeline(pipeline.id)

    async def update_pipeline(
        self, pipeline_id: UUID, payload: PipelineUpdate, actor: User
    ) -> Pipeline:
        self.auth.require("settings.manage")
        pipeline = await self._load(pipeline_id)

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            return pipeline

        # Promoting a new default must demote the old one first, or the partial
        # unique index rejects the write.
        if updates.get("is_default") is True and not pipeline.is_default:
            await self._demote_existing_default()
        if updates.get("is_default") is False and pipeline.is_default:
            raise ConflictError(
                "Promote another pipeline to default instead of clearing this "
                "one — a workspace with no default has nowhere to put new deals."
            )

        for field, value in updates.items():
            setattr(pipeline, field, value)
        pipeline.updated_by = actor.id
        await self._flush_translating_conflicts()

        await self.audit.record(
            action=AuditAction.RECORD_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=pipeline.id,
            metadata={"changes": {k: str(v) for k, v in updates.items()}},
        )
        return await self.get_pipeline(pipeline.id)

    async def delete_pipeline(self, pipeline_id: UUID, actor: User) -> None:
        """Soft delete. Refused while the pipeline still holds deals."""
        self.auth.require("settings.manage")
        pipeline = await self._load(pipeline_id)

        for stage in pipeline.stages:
            held = await self.deals.count_open_in_stage(
                stage.id, self.auth.organization_id
            )
            if held:
                raise ConflictError(
                    f"'{stage.name}' still holds {held} deal(s). "
                    "Move them to another pipeline first."
                )

        if pipeline.is_default:
            raise ConflictError(
                "This is the default pipeline. Promote another one first."
            )

        from datetime import UTC, datetime

        pipeline.deleted_at = datetime.now(UTC)
        pipeline.updated_by = actor.id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=pipeline.id,
            metadata={"name": pipeline.name},
        )

    # ------------------------------------------------------------- stages

    async def add_stage(
        self, pipeline_id: UUID, payload: PipelineStageCreate, actor: User
    ) -> Pipeline:
        self.auth.require("settings.manage")
        pipeline = await self._load(pipeline_id)

        if payload.is_won and any(stage.is_won for stage in pipeline.stages):
            raise ConflictError("This pipeline already has a winning stage.")
        if payload.is_lost and any(stage.is_lost for stage in pipeline.stages):
            raise ConflictError("This pipeline already has a losing stage.")

        self.session.add(
            PipelineStage(
                organization_id=self.auth.organization_id,
                pipeline_id=pipeline.id,
                key=payload.key,
                name=payload.name,
                position=payload.position,
                default_probability=payload.default_probability,
                is_won=payload.is_won,
                is_lost=payload.is_lost,
            )
        )
        await self._flush_translating_conflicts()

        await self.audit.record(
            action=AuditAction.RECORD_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=pipeline.id,
            metadata={"stage_added": payload.key},
        )
        return await self.get_pipeline(pipeline.id)

    async def update_stage(
        self,
        pipeline_id: UUID,
        stage_id: UUID,
        payload: PipelineStageUpdate,
        actor: User,
    ) -> Pipeline:
        self.auth.require("settings.manage")
        pipeline = await self._load(pipeline_id)

        stage = next((s for s in pipeline.stages if s.id == stage_id), None)
        if stage is None:
            raise NotFoundError("Stage not found in this pipeline.")

        updates = payload.model_dump(exclude_unset=True)
        if updates.get("is_won") and any(
            other.is_won for other in pipeline.stages if other.id != stage_id
        ):
            raise ConflictError("This pipeline already has a winning stage.")
        if updates.get("is_lost") and any(
            other.is_lost for other in pipeline.stages if other.id != stage_id
        ):
            raise ConflictError("This pipeline already has a losing stage.")

        for field, value in updates.items():
            setattr(stage, field, value)
        await self._flush_translating_conflicts()

        await self.audit.record(
            action=AuditAction.RECORD_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=pipeline.id,
            metadata={"stage_updated": stage.key, "changes": list(updates)},
        )
        return await self.get_pipeline(pipeline.id)

    async def delete_stage(
        self, pipeline_id: UUID, stage_id: UUID, actor: User
    ) -> Pipeline:
        """Remove a stage. Refused while it still holds deals.

        The foreign key is RESTRICT, so the database would refuse anyway — but
        "12 deals are still in this stage" is actionable and a foreign-key
        violation is not.
        """
        self.auth.require("settings.manage")
        pipeline = await self._load(pipeline_id)

        stage = next((s for s in pipeline.stages if s.id == stage_id), None)
        if stage is None:
            raise NotFoundError("Stage not found in this pipeline.")

        if len(pipeline.stages) <= 1:
            raise ConflictError(
                "A pipeline needs at least one stage. Add another before "
                "removing this one."
            )

        held = await self.deals.count_open_in_stage(stage.id, self.auth.organization_id)
        if held:
            raise ConflictError(
                f"This stage still holds {held} deal(s). Move them first."
            )

        await self.session.delete(stage)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=pipeline.id,
            metadata={"stage_removed": stage.key},
        )
        return await self.get_pipeline(pipeline.id)

    # ------------------------------------------------------------ helpers

    async def _load(self, pipeline_id: UUID) -> Pipeline:
        pipeline = await self.pipelines.get_with_stages(
            pipeline_id, self.auth.organization_id
        )
        if pipeline is None:
            raise NotFoundError("Pipeline not found.")
        return pipeline

    async def _demote_existing_default(self) -> None:
        """Clear the current default so the partial unique index stays happy."""
        await self.session.execute(
            update(Pipeline)
            .where(Pipeline.organization_id == self.auth.organization_id)
            .where(Pipeline.is_default)
            .values(is_default=False)
        )
        await self.session.flush()

    async def _flush_translating_conflicts(self) -> None:
        """Turn constraint violations into 409s rather than 500s."""
        try:
            await self.session.flush()
        except IntegrityError as exc:
            detail = str(exc.orig)
            if "uq_pipelines_org_name" in detail:
                raise ConflictError(
                    "A pipeline with this name already exists."
                ) from exc
            if "uq_pipelines_one_default" in detail:
                raise ConflictError(
                    "Another pipeline is already the default."
                ) from exc
            if "uq_pipeline_stages_key" in detail:
                raise ConflictError(
                    "A stage with this key already exists in this pipeline."
                ) from exc
            raise
