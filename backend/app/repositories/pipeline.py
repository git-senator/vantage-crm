"""Pipeline data access.

Pipelines are workspace configuration rather than scoped CRM data, so there is
no owner predicate here — every member of a tenant sees the same pipelines.
Tenant scoping still applies, on top of RLS, for the same defence-in-depth
reason as everywhere else.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import selectinload

from app.models.pipeline import Pipeline, PipelineStage
from app.repositories.base import BaseRepository


class PipelineRepository(BaseRepository[Pipeline]):
    model = Pipeline

    def _with_stages(self) -> Select[tuple[Pipeline]]:
        # selectinload, not joinedload: a pipeline has many stages, and a
        # joined load would multiply the pipeline row by its stage count and
        # need de-duplicating on every read.
        #
        # `populate_existing` is load-bearing. Adding or removing a stage and
        # then re-reading the pipeline in the same session returns the *stale*
        # collection otherwise: the Pipeline is already in the identity map
        # with `stages` loaded, and a fresh query does not overwrite loaded
        # state by default. The symptom is an API response that omits the stage
        # just created — which is exactly what the caller is checking for.
        return (
            self._base_query()
            .options(selectinload(Pipeline.stages))
            .execution_options(populate_existing=True)
        )

    async def list_all(self, organization_id: UUID) -> list[Pipeline]:
        query = self.scoped_to_organization(self._with_stages(), organization_id)
        query = query.order_by(Pipeline.is_default.desc(), Pipeline.name)
        return list((await self.session.execute(query)).unique().scalars().all())

    async def get_with_stages(
        self, pipeline_id: UUID, organization_id: UUID
    ) -> Pipeline | None:
        query = self.scoped_to_organization(
            self._with_stages().where(Pipeline.id == pipeline_id), organization_id
        )
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    async def get_default(self, organization_id: UUID) -> Pipeline | None:
        """The pipeline a new deal lands in when none is specified.

        Falls back to any pipeline when no default is flagged, so a workspace
        whose default was retired can still create deals rather than failing
        with a message nobody can act on.
        """
        query = self.scoped_to_organization(self._with_stages(), organization_id)
        query = query.order_by(Pipeline.is_default.desc(), Pipeline.created_at)
        return (await self.session.execute(query)).unique().scalars().first()

    async def get_stage(
        self, stage_id: UUID, organization_id: UUID
    ) -> PipelineStage | None:
        """Fetch a stage, scoped to the tenant.

        `pipeline_stages` carries `organization_id` directly for exactly this —
        resolving a stage without joining through its pipeline first.
        """
        query = (
            select(PipelineStage)
            .where(PipelineStage.id == stage_id)
            .where(PipelineStage.organization_id == organization_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def first_stage(
        self, pipeline_id: UUID, organization_id: UUID
    ) -> PipelineStage | None:
        """The entry stage of a pipeline: lowest position, ties broken on key.

        Ties are possible because `position` is deliberately not unique — see
        the model docstring — so the ordering needs a deterministic second key
        or the entry stage would vary between requests.
        """
        query = (
            select(PipelineStage)
            .where(PipelineStage.pipeline_id == pipeline_id)
            .where(PipelineStage.organization_id == organization_id)
            .order_by(PipelineStage.position, PipelineStage.key)
            .limit(1)
        )
        return (await self.session.execute(query)).scalars().first()

    async def count_stages(self, pipeline_id: UUID, organization_id: UUID) -> int:
        from sqlalchemy import func

        query = (
            select(func.count())
            .select_from(PipelineStage)
            .where(PipelineStage.pipeline_id == pipeline_id)
            .where(PipelineStage.organization_id == organization_id)
        )
        return int((await self.session.execute(query)).scalar() or 0)
