"""Pipelines — workspace configuration, not CRM data.

The rules worth pinning down are all about not breaking live deals: a pipeline
must always have somewhere to put a deal, exactly one default, at most one
winning stage, and no way to retire a stage that still holds transactions.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.organization import Organization
from app.schemas.client import ClientCreate
from app.schemas.deal import DealCreate
from app.schemas.pipeline import (
    PipelineCreate,
    PipelineStageCreate,
    PipelineStageUpdate,
    PipelineUpdate,
)
from app.services.client import ClientService
from app.services.deal import DealService
from app.services.pipeline import PipelineService, build_default_pipeline
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

pytestmark = pytest.mark.integration


@pytest.fixture
async def seeded_pipeline(db: AsyncSession, organization: Organization):  # type: ignore[no-untyped-def]
    """The default pipeline, as the migration and bootstrap both create it."""
    pipeline = build_default_pipeline(organization.id)
    db.add(pipeline)
    await db.flush()
    return pipeline


def _stages(**overrides: object) -> list[PipelineStageCreate]:
    base = [
        PipelineStageCreate(key="intake", name="Intake", position=0),
        PipelineStageCreate(
            key="won", name="Won", position=1, is_won=True, default_probability=100
        ),
    ]
    return base


class TestDefaultPipeline:
    async def test_builder_produces_a_usable_funnel(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        pipeline = build_default_pipeline(organization.id)
        db.add(pipeline)
        await db.flush()

        keys = [stage.key for stage in pipeline.stages]
        assert keys[0] == "qualification"
        assert pipeline.is_default is True

    async def test_has_exactly_one_won_and_one_lost_stage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """A funnel with no losing stage reports 100% conversion forever."""
        pipeline = build_default_pipeline(organization.id)
        db.add(pipeline)
        await db.flush()

        assert sum(s.is_won for s in pipeline.stages) == 1
        assert sum(s.is_lost for s in pipeline.stages) == 1

    async def test_stages_carry_the_organization_directly(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """The column RLS keys on. Reachable via pipeline_id is not enough."""
        pipeline = build_default_pipeline(organization.id)
        db.add(pipeline)
        await db.flush()

        assert all(s.organization_id == organization.id for s in pipeline.stages)


class TestReadPermissions:
    async def test_reading_requires_deals_view(
        self, db: AsyncSession, organization: Organization, seeded_pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "nobody@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("none",),
            grants={},
        )
        with pytest.raises(PermissionDeniedError):
            await PipelineService(db, auth).list_pipelines()

    async def test_an_agent_can_read_pipelines(
        self, db: AsyncSession, agent, seeded_pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        """The board cannot render without stages, so view is enough."""
        _user, auth = agent
        rows = await PipelineService(db, auth).list_pipelines()
        assert len(rows) == 1

    async def test_an_agent_cannot_write_pipelines(
        self, db: AsyncSession, agent
    ) -> None:  # type: ignore[no-untyped-def]
        """Configuration is settings.manage — an agent must not be able to
        delete the stage a colleague's deals sit in."""
        user, auth = agent
        with pytest.raises(PermissionDeniedError):
            await PipelineService(db, auth).create_pipeline(
                PipelineCreate(name="Rogue", stages=_stages()), user
            )


class TestCreate:
    async def test_admin_creates_a_pipeline_with_stages(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        pipeline = await PipelineService(db, auth).create_pipeline(
            PipelineCreate(name="Lettings", stages=_stages()), user
        )
        assert pipeline.name == "Lettings"
        assert len(pipeline.stages) == 2

    async def test_a_pipeline_needs_at_least_one_stage(self) -> None:
        with pytest.raises(ValueError):
            PipelineCreate(name="Empty", stages=[])

    async def test_duplicate_stage_keys_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="unique"):
            PipelineCreate(
                name="Dup",
                stages=[
                    PipelineStageCreate(key="a", name="A"),
                    PipelineStageCreate(key="a", name="Also A"),
                ],
            )

    async def test_two_winning_stages_are_rejected(self) -> None:
        """Two ways to win makes 'did we win?' ambiguous, and every conversion
        metric downstream depends on that answer."""
        with pytest.raises(ValueError, match="at most one winning"):
            PipelineCreate(
                name="Two wins",
                stages=[
                    PipelineStageCreate(key="a", name="A", is_won=True),
                    PipelineStageCreate(key="b", name="B", is_won=True),
                ],
            )

    async def test_a_stage_cannot_be_both_won_and_lost(self) -> None:
        with pytest.raises(ValueError, match="both won and lost"):
            PipelineStageCreate(key="a", name="A", is_won=True, is_lost=True)

    async def test_stage_key_must_be_a_machine_name(self) -> None:
        """Analytics group by key, so it has to be stable and predictable."""
        with pytest.raises(ValueError):
            PipelineStageCreate(key="Under Contract!", name="Under contract")

    async def test_duplicate_pipeline_name_is_a_conflict(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PipelineService(db, auth)
        await service.create_pipeline(
            PipelineCreate(name="Lettings", stages=_stages()), user
        )
        with pytest.raises(ConflictError):
            await service.create_pipeline(
                PipelineCreate(name="Lettings", stages=_stages()), user
            )


class TestDefaultFlag:
    async def test_promoting_a_new_default_demotes_the_old_one(
        self, db: AsyncSession, admin, seeded_pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        """Two defaults means new deals land in whichever the planner picks."""
        user, auth = admin
        service = PipelineService(db, auth)

        promoted = await service.create_pipeline(
            PipelineCreate(name="Lettings", is_default=True, stages=_stages()), user
        )
        assert promoted.is_default is True

        original = await service.get_pipeline(seeded_pipeline.id)
        assert original.is_default is False

    async def test_the_default_cannot_simply_be_cleared(
        self, db: AsyncSession, admin, seeded_pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        """A workspace with no default has nowhere to put new deals."""
        user, auth = admin
        with pytest.raises(ConflictError):
            await PipelineService(db, auth).update_pipeline(
                seeded_pipeline.id, PipelineUpdate(is_default=False), user
            )

    async def test_resolve_default_returns_the_flagged_one(
        self, db: AsyncSession, admin, seeded_pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        _user, auth = admin
        resolved = await PipelineService(db, auth).resolve_default()
        assert resolved.id == seeded_pipeline.id

    async def test_resolve_default_without_any_pipeline_is_actionable(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The error has to tell someone what to do about it."""
        _user, auth = admin
        with pytest.raises(ConflictError, match="administrator"):
            await PipelineService(db, auth).resolve_default()


class TestStages:
    async def test_add_a_stage(self, db: AsyncSession, admin, seeded_pipeline) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        updated = await PipelineService(db, auth).add_stage(
            seeded_pipeline.id,
            PipelineStageCreate(key="inspection", name="Inspection", position=3),
            user,
        )
        assert "inspection" in {stage.key for stage in updated.stages}

    async def test_a_second_winning_stage_is_refused(
        self, db: AsyncSession, admin, seeded_pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        with pytest.raises(ConflictError, match="winning stage"):
            await PipelineService(db, auth).add_stage(
                seeded_pipeline.id,
                PipelineStageCreate(key="also_won", name="Also won", is_won=True),
                user,
            )

    async def test_rename_and_reorder(
        self, db: AsyncSession, admin, seeded_pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        stage = seeded_pipeline.stages[0]
        updated = await PipelineService(db, auth).update_stage(
            seeded_pipeline.id,
            stage.id,
            PipelineStageUpdate(name="Initial review", position=5),
            user,
        )
        renamed = next(s for s in updated.stages if s.id == stage.id)
        assert renamed.name == "Initial review"
        assert renamed.position == 5

    async def test_cannot_delete_a_stage_holding_deals(
        self, db: AsyncSession, admin, seeded_pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        """RESTRICT would refuse anyway, but '1 deal is still here' is
        actionable and a foreign-key error is not."""
        user, auth = admin
        client = await ClientService(db, auth).create_client(
            ClientCreate(first_name="Omar", last_name="Haddad"), user
        )
        await DealService(db, auth).create_deal(
            DealCreate(title="Live deal", client_id=client.id), user
        )

        entry = sorted(seeded_pipeline.stages, key=lambda s: s.position)[0]
        with pytest.raises(ConflictError, match="still holds"):
            await PipelineService(db, auth).delete_stage(
                seeded_pipeline.id, entry.id, user
            )

    async def test_can_delete_an_empty_stage(
        self, db: AsyncSession, admin, seeded_pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        target = next(s for s in seeded_pipeline.stages if s.key == "showing")

        updated = await PipelineService(db, auth).delete_stage(
            seeded_pipeline.id, target.id, user
        )
        assert "showing" not in {stage.key for stage in updated.stages}

    async def test_cannot_delete_the_last_stage(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PipelineService(db, auth)
        pipeline = await service.create_pipeline(
            PipelineCreate(
                name="Single", stages=[PipelineStageCreate(key="only", name="Only")]
            ),
            user,
        )
        with pytest.raises(ConflictError, match="at least one stage"):
            await service.delete_stage(pipeline.id, pipeline.stages[0].id, user)


class TestTenantIsolation:
    async def test_pipelines_never_cross_organizations(
        self,
        db: AsyncSession,
        admin,
        other_organization: Organization,
        seeded_pipeline,
    ) -> None:  # type: ignore[no-untyped-def]
        _user, admin_auth = admin
        foreign = build_default_pipeline(other_organization.id)
        db.add(foreign)
        await db.flush()

        rows = await PipelineService(db, admin_auth).list_pipelines()
        assert [row.id for row in rows] == [seeded_pipeline.id]

    async def test_a_foreign_pipeline_is_404(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        _user, auth = admin
        foreign = build_default_pipeline(other_organization.id)
        db.add(foreign)
        await db.flush()

        with pytest.raises(NotFoundError):
            await PipelineService(db, auth).get_pipeline(foreign.id)

    async def test_a_foreign_stage_does_not_resolve(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """`get_stage` is what deal creation trusts; it must be tenant-scoped."""
        from app.repositories.pipeline import PipelineRepository

        _user, auth = admin
        foreign = build_default_pipeline(other_organization.id)
        db.add(foreign)
        await db.flush()

        found = await PipelineRepository(db).get_stage(
            foreign.stages[0].id, auth.organization_id
        )
        assert found is None


class TestScopeSanity:
    async def test_settings_manage_is_the_write_gate(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """Documents the deliberate choice: pipelines are configuration, so
        deals.manage is not sufficient to reconfigure them."""
        user = await make_user(db, organization, "dealmaker@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("dealmaker",),
            grants={"deals.manage": Scope.ALL, "deals.view": Scope.ALL},
        )
        with pytest.raises(PermissionDeniedError):
            await PipelineService(db, auth).create_pipeline(
                PipelineCreate(name="Nope", stages=_stages()), user
            )
