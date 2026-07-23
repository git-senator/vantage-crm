"""Deals — the central business entity.

The CRUD properties are the same ones every entity needs and are tested the
same way. What is specific to deals, and what most of this file is about:

  * **stage transitions are a domain action** — they write history, reset
    probability, set or clear the close date and emit an activity, atomically,
    and a PATCH cannot do any of it;
  * **`status` is derived** from the stage, so it cannot disagree with the
    board;
  * **commission has a rule** — the amount is authoritative and is never
    silently recomputed over somebody's negotiated figure;
  * **history is the analytics substrate** and must be complete from the
    moment a deal is created.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.activity import Activity
from app.models.audit import AuditLog
from app.models.deal import Deal, DealStageHistory
from app.models.organization import Organization
from app.models.rbac import Team, TeamMember
from app.schemas.client import ClientCreate
from app.schemas.common import Cursor
from app.schemas.deal import (
    DealCreate,
    DealFilters,
    DealStageTransition,
    DealUpdate,
)
from app.schemas.property import PropertyCreate
from app.services.client import ClientService
from app.services.deal import DealService, compute_commission
from app.services.pipeline import build_default_pipeline
from app.services.property import PropertyService
from app.services.rbac import AuthorizationContext
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


@pytest.fixture
async def pipeline(db: AsyncSession, organization: Organization):  # type: ignore[no-untyped-def]
    built = build_default_pipeline(organization.id)
    db.add(built)
    await db.flush()
    return built


def stage_by_key(pipeline, key: str):  # type: ignore[no-untyped-def]
    return next(stage for stage in pipeline.stages if stage.key == key)


@pytest.fixture
async def client_record(db: AsyncSession, admin):  # type: ignore[no-untyped-def]
    user, auth = admin
    return await ClientService(db, auth).create_client(
        ClientCreate(first_name="Omar", last_name="Haddad", type="seller"), user
    )


def _payload(client_id, **overrides: object) -> DealCreate:  # type: ignore[no-untyped-def]
    data: dict = {
        "title": "1428 Sanchez — Lindqvist purchase",
        "client_id": client_id,
        "value": Decimal("1895000.00"),
        "commission_rate": Decimal("0.0250"),
        "priority": "high",
        **overrides,
    }
    return DealCreate(**data)


# --------------------------------------------------------------------- tests


class TestCreate:
    async def test_lands_in_the_default_pipeline_first_stage(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """The quick-add path: no pipeline or stage supplied."""
        user, auth = admin
        deal = await DealService(db, auth).create_deal(
            _payload(client_record.id), user
        )

        assert deal.pipeline_id == pipeline.id
        assert deal.stage.key == "qualification"
        assert deal.owner_id == user.id

    async def test_probability_defaults_to_the_stage(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        deal = await DealService(db, auth).create_deal(
            _payload(client_record.id), user
        )
        assert deal.probability == 10  # qualification's default

    async def test_writes_the_opening_history_row(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """Without it the first transition has no baseline to measure from."""
        user, auth = admin
        deal = await DealService(db, auth).create_deal(
            _payload(client_record.id), user
        )

        rows = (
            await db.execute(
                select(DealStageHistory).where(DealStageHistory.deal_id == deal.id)
            )
        ).unique().scalars().all()

        assert len(rows) == 1
        assert rows[0].from_stage_id is None
        assert rows[0].to_stage_id == deal.stage_id
        assert rows[0].duration_in_stage is None

    async def test_a_deal_requires_a_client(self, client_record) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(ValueError):
            DealCreate(title="Clientless")  # type: ignore[call-arg]

    async def test_client_must_belong_to_the_workspace(
        self, db: AsyncSession, admin, pipeline, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        outsider = await make_user(db, other_organization, "outsider@meridian.example")
        outsider_auth = AuthorizationContext(
            user_id=outsider.id,
            organization_id=other_organization.id,
            role_keys=("admin",),
            grants={"contacts.manage": Scope.ALL, "contacts.view": Scope.ALL},
        )
        foreign_client = await ClientService(db, outsider_auth).create_client(
            ClientCreate(company_name="Meridian Holdings"), outsider
        )

        with pytest.raises(NotFoundError):
            await DealService(db, auth).create_deal(
                _payload(foreign_client.id), user
            )

    async def test_optional_property_is_linked(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        listing = await PropertyService(db, auth).create_property(
            PropertyCreate(
                title="1428 Sanchez",
                address_line1="1428 Sanchez Street",
                city="San Francisco",
                state="CA",
                postal_code="94131",
            ),
            user,
        )
        deal = await DealService(db, auth).create_deal(
            _payload(client_record.id, property_id=listing.id), user
        )
        assert deal.listing is not None
        assert deal.listing.id == listing.id

    async def test_requires_the_manage_permission(
        self, db: AsyncSession, organization: Organization, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "viewer@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("viewer",),
            grants={"deals.view": Scope.ALL},
        )
        with pytest.raises(PermissionDeniedError):
            await DealService(db, auth).create_deal(_payload(client_record.id), user)

    async def test_without_a_pipeline_the_error_is_actionable(
        self, db: AsyncSession, admin, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """No `pipeline` fixture here — the workspace has none."""
        user, auth = admin
        with pytest.raises(ConflictError, match="administrator"):
            await DealService(db, auth).create_deal(_payload(client_record.id), user)


class TestCommission:
    def test_rate_times_value_rounds_half_up(self) -> None:
        """Banker's rounding would produce cheques a cent off the contract."""
        assert compute_commission(Decimal("100.10"), Decimal("0.0250")) == Decimal(
            "2.50"
        )
        assert compute_commission(Decimal("1895000"), Decimal("0.0250")) == Decimal(
            "47375.00"
        )

    def test_is_null_without_a_value_or_rate(self) -> None:
        assert compute_commission(None, Decimal("0.025")) is None
        assert compute_commission(Decimal("100"), None) is None

    async def test_amount_derives_from_rate_when_omitted(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        deal = await DealService(db, auth).create_deal(
            _payload(client_record.id), user
        )
        assert deal.commission_amount == Decimal("47375.00")

    async def test_an_explicit_amount_wins(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """Flat fees are real and must survive contact with the rate."""
        user, auth = admin
        deal = await DealService(db, auth).create_deal(
            _payload(client_record.id, commission_amount=Decimal("15000.00")), user
        )
        assert deal.commission_amount == Decimal("15000.00")

    async def test_a_stored_amount_is_not_recomputed_by_an_unrelated_edit(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """The negotiated figure must not evaporate when the title changes."""
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(
            _payload(client_record.id, commission_amount=Decimal("15000.00")), user
        )

        updated = await service.update_deal(
            deal.id, DealUpdate(title="Renamed"), user
        )
        assert updated.commission_amount == Decimal("15000.00")

    async def test_changing_the_rate_recomputes_the_amount(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        updated = await service.update_deal(
            deal.id, DealUpdate(commission_rate=Decimal("0.0300")), user
        )
        assert updated.commission_amount == Decimal("56850.00")

    async def test_setting_both_keeps_the_explicit_amount(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        updated = await service.update_deal(
            deal.id,
            DealUpdate(
                commission_rate=Decimal("0.0300"),
                commission_amount=Decimal("20000.00"),
            ),
            user,
        )
        assert updated.commission_amount == Decimal("20000.00")

    async def test_a_rate_above_one_is_rejected(self, client_record) -> None:  # type: ignore[no-untyped-def]
        """2.5 is a percentage somebody forgot to divide by 100."""
        with pytest.raises(ValueError):
            _payload(client_record.id, commission_rate=Decimal("2.5"))


class TestDerivedStatus:
    async def test_an_open_deal_reports_open(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        deal = await DealService(db, auth).create_deal(
            _payload(client_record.id), user
        )
        assert deal.status == "open"
        assert deal.is_closed is False

    async def test_status_follows_the_stage(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """Derived, so it cannot disagree with the board."""
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        won = await service.move_stage(
            deal.id,
            DealStageTransition(to_stage_id=stage_by_key(pipeline, "closed_won").id),
            user,
        )
        assert won.status == "won"

        lost = await service.move_stage(
            deal.id,
            DealStageTransition(
                to_stage_id=stage_by_key(pipeline, "closed_lost").id,
                lost_reason="Financing fell through",
            ),
            user,
        )
        assert lost.status == "lost"

    async def test_weighted_value_is_value_times_probability(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        deal = await DealService(db, auth).create_deal(
            _payload(client_record.id), user
        )
        # 1,895,000 at qualification's 10%
        assert deal.weighted_value == Decimal("189500.00")

    async def test_weighted_value_is_null_when_unpriced(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """Returning 0 would understate the pipeline silently."""
        user, auth = admin
        deal = await DealService(db, auth).create_deal(
            _payload(client_record.id, value=None, commission_rate=None), user
        )
        assert deal.weighted_value is None


class TestStageTransitions:
    async def test_moving_writes_history_with_a_duration(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        await service.move_stage(
            deal.id,
            DealStageTransition(to_stage_id=stage_by_key(pipeline, "showing").id),
            user,
        )

        rows = await service.stage_history(deal.id)
        assert len(rows) == 2
        latest = rows[0]
        assert latest.from_stage.key == "qualification"
        assert latest.to_stage.key == "showing"
        # Measured from the creation row, so it is real but tiny.
        assert latest.duration_in_stage is not None
        assert latest.duration_in_stage.total_seconds() >= 0

    async def test_probability_resets_to_the_new_stage_default(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        moved = await service.move_stage(
            deal.id,
            DealStageTransition(to_stage_id=stage_by_key(pipeline, "closing").id),
            user,
        )
        assert moved.probability == 90

    async def test_probability_can_be_overridden(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        moved = await service.move_stage(
            deal.id,
            DealStageTransition(
                to_stage_id=stage_by_key(pipeline, "closing").id, probability=55
            ),
            user,
        )
        assert moved.probability == 55

    async def test_winning_sets_the_close_date(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        won = await service.move_stage(
            deal.id,
            DealStageTransition(to_stage_id=stage_by_key(pipeline, "closed_won").id),
            user,
        )
        # UTC, not the host's local date. Every other timestamp in the system is
        # UTC and analytics compares this column against UTC period bounds, so a
        # local date would put a deal closed near midnight outside the very day
        # it closed on any server not running UTC.
        assert won.actual_close_date == datetime.now(UTC).date()

    async def test_reopening_clears_the_close_date_and_reason(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """A revived deal that keeps its close date reports as closed forever."""
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        await service.move_stage(
            deal.id,
            DealStageTransition(
                to_stage_id=stage_by_key(pipeline, "closed_lost").id,
                lost_reason="Financing fell through",
            ),
            user,
        )
        reopened = await service.move_stage(
            deal.id,
            DealStageTransition(to_stage_id=stage_by_key(pipeline, "offer").id),
            user,
        )

        assert reopened.status == "open"
        assert reopened.actual_close_date is None
        assert reopened.lost_reason is None

    async def test_losing_requires_a_reason(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """'Why do we lose deals' is the question the pipeline exists to answer."""
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        with pytest.raises(ConflictError, match="reason is required"):
            await service.move_stage(
                deal.id,
                DealStageTransition(
                    to_stage_id=stage_by_key(pipeline, "closed_lost").id
                ),
                user,
            )

    async def test_whitespace_is_not_a_reason(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        with pytest.raises(ConflictError):
            await service.move_stage(
                deal.id,
                DealStageTransition(
                    to_stage_id=stage_by_key(pipeline, "closed_lost").id,
                    lost_reason="   ",
                ),
                user,
            )

    async def test_moving_to_the_current_stage_is_a_conflict(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """A no-op transition would write a history row saying nothing happened."""
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        with pytest.raises(ConflictError, match="already in that stage"):
            await service.move_stage(
                deal.id, DealStageTransition(to_stage_id=deal.stage_id), user
            )

    async def test_cannot_move_into_another_pipelines_stage(
        self, db: AsyncSession, admin, pipeline, client_record, organization
    ) -> None:  # type: ignore[no-untyped-def]
        """Silently re-homing a deal corrupts both funnels' analytics."""
        from app.schemas.pipeline import PipelineCreate, PipelineStageCreate
        from app.services.pipeline import PipelineService

        user, auth = admin
        other = await PipelineService(db, auth).create_pipeline(
            PipelineCreate(
                name="Lettings",
                stages=[PipelineStageCreate(key="intake", name="Intake")],
            ),
            user,
        )
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        with pytest.raises(ConflictError, match="different pipeline"):
            await service.move_stage(
                deal.id,
                DealStageTransition(to_stage_id=other.stages[0].id),
                user,
            )

    async def test_a_foreign_stage_is_404(
        self, db: AsyncSession, admin, pipeline, client_record, other_organization
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        foreign = build_default_pipeline(other_organization.id)
        db.add(foreign)
        await db.flush()

        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        with pytest.raises(NotFoundError):
            await service.move_stage(
                deal.id,
                DealStageTransition(to_stage_id=foreign.stages[1].id),
                user,
            )

    async def test_update_cannot_change_the_stage(self) -> None:
        """The whole reason transitions are a separate endpoint."""
        assert "stage_id" not in DealUpdate.model_fields


class TestTransitionSideEffects:
    async def test_emits_a_stage_change_activity(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        await service.move_stage(
            deal.id,
            DealStageTransition(
                to_stage_id=stage_by_key(pipeline, "showing").id,
                note="Buyer viewed on Saturday",
            ),
            user,
        )

        activity = (
            await db.execute(
                select(Activity).where(Activity.entity_id == deal.id)
            )
        ).unique().scalar_one()

        assert activity.type == "stage_change"
        assert activity.entity_type == "deal"
        assert "Showing" in activity.subject
        assert activity.body == "Buyer viewed on Saturday"
        assert activity.metadata_["to_stage"] == "showing"

    async def test_audits_with_a_dedicated_action(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """Buried in a generic field diff it would be unqueryable."""
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        await service.move_stage(
            deal.id,
            DealStageTransition(to_stage_id=stage_by_key(pipeline, "showing").id),
            user,
        )

        entry = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.RECORD_STAGE_CHANGED
                )
            )
        ).scalar_one()
        assert entry.entity_type == "deal"
        assert entry.metadata_["to_stage"] == "showing"

    async def test_a_rejected_transition_leaves_nothing_behind(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """No history, no activity, no audit for a move that did not happen."""
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        with pytest.raises(ConflictError):
            await service.move_stage(
                deal.id,
                DealStageTransition(
                    to_stage_id=stage_by_key(pipeline, "closed_lost").id
                ),
                user,
            )

        history = await service.stage_history(deal.id)
        assert len(history) == 1  # just the creation row

        activities = (
            await db.execute(select(func.count()).select_from(Activity))
        ).scalar()
        assert activities == 0

    async def test_timeline_reads_back_the_activity(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)
        await service.move_stage(
            deal.id,
            DealStageTransition(to_stage_id=stage_by_key(pipeline, "offer").id),
            user,
        )

        timeline = await service.timeline(deal.id)
        assert len(timeline) == 1


class TestConcurrentTransitions:
    async def test_two_simultaneous_moves_produce_consistent_history(
        self, engine, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """Two drags of the same card, on separate connections.

        Without the row lock both transactions read the same "current stage"
        and the same last-history-row baseline, and both write a history row
        claiming to have left `qualification` — so the deal appears to have
        left one stage twice, and cycle-time analytics double-counts it.

        With the lock they serialise: one wins, the other either moves from the
        already-updated stage or is rejected as a no-op. Either way the history
        stays a coherent chain.
        """
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)
        await db.commit()

        showing_id = stage_by_key(pipeline, "showing").id
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)

        async def move() -> str:
            async with factory() as session:
                try:
                    await DealService(session, auth).move_stage(
                        deal.id,
                        DealStageTransition(to_stage_id=showing_id),
                        user,
                    )
                    await session.commit()
                    return "moved"
                except ConflictError:
                    await session.rollback()
                    return "conflict"

        results = await asyncio.gather(move(), move())

        assert sorted(results) == ["conflict", "moved"], (
            f"Expected exactly one move and one conflict, got {results}."
        )

        async with factory() as session:
            rows = (
                await session.execute(
                    select(DealStageHistory)
                    .where(DealStageHistory.deal_id == deal.id)
                    .order_by(DealStageHistory.changed_at)
                )
            ).unique().scalars().all()

            # Creation row plus exactly one transition.
            assert len(rows) == 2
            # The chain is coherent: each row leaves where the last one landed.
            assert rows[0].to_stage_id == rows[1].from_stage_id


class TestScopeIsAWhereClause:
    async def test_agent_sees_only_their_own_deals(
        self, db: AsyncSession, agent, other_agent, pipeline, organization
    ) -> None:  # type: ignore[no-untyped-def]
        """Unlike properties, a deal is a personal book of business."""
        mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent

        client = await ClientService(
            db,
            AuthorizationContext(
                user_id=mine_user.id,
                organization_id=organization.id,
                role_keys=("admin",),
                grants={"contacts.manage": Scope.ALL, "contacts.view": Scope.ALL},
            ),
        ).create_client(ClientCreate(first_name="Shared", last_name="Client"), mine_user)

        await DealService(db, mine_auth).create_deal(
            _payload(client.id, title="Mine"), mine_user
        )
        await DealService(db, theirs_auth).create_deal(
            _payload(client.id, title="Theirs"), theirs_user
        )

        rows, _ = await DealService(db, mine_auth).list_deals(
            filters=DealFilters(), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Mine"]

    async def test_out_of_scope_deal_is_404_not_403(
        self, db: AsyncSession, agent, other_agent, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        _mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent
        theirs = await DealService(db, theirs_auth).create_deal(
            _payload(client_record.id), theirs_user
        )

        with pytest.raises(NotFoundError):
            await DealService(db, mine_auth).get_deal(theirs.id)

    async def test_cannot_move_a_deal_outside_scope(
        self, db: AsyncSession, agent, other_agent, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        _mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent
        theirs = await DealService(db, theirs_auth).create_deal(
            _payload(client_record.id), theirs_user
        )

        with pytest.raises(NotFoundError):
            await DealService(db, mine_auth).move_stage(
                theirs.id,
                DealStageTransition(to_stage_id=stage_by_key(pipeline, "offer").id),
                theirs_user,
            )

    async def test_manager_sees_the_team(
        self, db: AsyncSession, organization: Organization, pipeline, rbac_seeded: None
    ) -> None:
        manager = await make_user(db, organization, "manager@vantage.example")
        member = await make_user(db, organization, "member@vantage.example")
        outsider = await make_user(db, organization, "solo@vantage.example")

        team = Team(organization_id=organization.id, name="Westside")
        db.add(team)
        await db.flush()
        for person in (manager, member):
            db.add(
                TeamMember(
                    team_id=team.id,
                    user_id=person.id,
                    organization_id=organization.id,
                )
            )
        await db.flush()

        manager_auth = await auth_for(db, manager, "manager")
        member_auth = await auth_for(db, member, "agent")
        outsider_auth = await auth_for(db, outsider, "agent")

        client = await ClientService(db, manager_auth).create_client(
            ClientCreate(first_name="Team", last_name="Client"), manager
        )

        await DealService(db, member_auth).create_deal(
            _payload(client.id, title="TeamDeal"), member
        )
        await DealService(db, outsider_auth).create_deal(
            _payload(client.id, title="OutsideTeam"), outsider
        )

        rows, _ = await DealService(db, manager_auth).list_deals(
            filters=DealFilters(), limit=50, cursor=None
        )
        titles = {r.title for r in rows}
        assert "TeamDeal" in titles
        assert "OutsideTeam" not in titles


class TestTenantIsolation:
    async def test_deals_never_cross_organizations(
        self, db: AsyncSession, admin, pipeline, client_record, other_organization
    ) -> None:  # type: ignore[no-untyped-def]
        _user, admin_auth = admin
        outsider = await make_user(db, other_organization, "outsider@meridian.example")
        outsider_auth = AuthorizationContext(
            user_id=outsider.id,
            organization_id=other_organization.id,
            role_keys=("admin",),
            grants={
                "deals.view": Scope.ALL,
                "deals.manage": Scope.ALL,
                "contacts.manage": Scope.ALL,
                "contacts.view": Scope.ALL,
            },
        )
        foreign_pipeline = build_default_pipeline(other_organization.id)
        db.add(foreign_pipeline)
        await db.flush()
        foreign_client = await ClientService(db, outsider_auth).create_client(
            ClientCreate(company_name="Meridian"), outsider
        )
        await DealService(db, outsider_auth).create_deal(
            _payload(foreign_client.id, title="Foreign"), outsider
        )

        rows, _ = await DealService(db, admin_auth).list_deals(
            filters=DealFilters(), limit=50, cursor=None
        )
        assert all(r.title != "Foreign" for r in rows)

    async def test_rls_blocks_an_unscoped_query(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await DealService(db, auth).create_deal(_payload(client_record.id), user)
        await db.commit()

        from app.db.sql_objects import tenant_policy_statements

        for statement in tenant_policy_statements("deals"):
            await db.execute(text(statement))
        await db.commit()

        try:
            async with db.begin():
                rows = (await db.execute(select(Deal))).unique().scalars().all()
            assert rows == [], (
                "An unscoped query returned rows with no tenant context bound. "
                "RLS is not enforcing on deals."
            )
        finally:
            await db.execute(text("DROP POLICY IF EXISTS tenant_isolation ON deals"))
            await db.execute(text("ALTER TABLE deals NO FORCE ROW LEVEL SECURITY"))
            await db.execute(text("ALTER TABLE deals DISABLE ROW LEVEL SECURITY"))
            await db.commit()

    async def test_rls_blocks_unscoped_history(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """History is analytics data and leaks the same information a deal does."""
        user, auth = admin
        await DealService(db, auth).create_deal(_payload(client_record.id), user)
        await db.commit()

        from app.db.sql_objects import tenant_policy_statements

        for statement in tenant_policy_statements("deal_stage_history"):
            await db.execute(text(statement))
        await db.commit()

        try:
            async with db.begin():
                rows = (
                    await db.execute(select(DealStageHistory))
                ).unique().scalars().all()
            assert rows == []
        finally:
            await db.execute(
                text("DROP POLICY IF EXISTS tenant_isolation ON deal_stage_history")
            )
            await db.execute(
                text("ALTER TABLE deal_stage_history NO FORCE ROW LEVEL SECURITY")
            )
            await db.execute(
                text("ALTER TABLE deal_stage_history DISABLE ROW LEVEL SECURITY")
            )
            await db.commit()


class TestFiltersAndSearch:
    async def test_filter_by_derived_status(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """`status` is not a column, so this exercises the EXISTS correlation."""
        user, auth = admin
        service = DealService(db, auth)
        open_deal = await service.create_deal(
            _payload(client_record.id, title="Open"), user
        )
        won_deal = await service.create_deal(
            _payload(client_record.id, title="Won"), user
        )
        await service.move_stage(
            won_deal.id,
            DealStageTransition(to_stage_id=stage_by_key(pipeline, "closed_won").id),
            user,
        )

        rows, _ = await service.list_deals(
            filters=DealFilters(status="won"), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Won"]

        rows, _ = await service.list_deals(
            filters=DealFilters(status="open"), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Open"]
        assert open_deal.id == rows[0].id

    async def test_filter_by_stage_and_priority(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        await service.create_deal(
            _payload(client_record.id, title="High", priority="high"), user
        )
        await service.create_deal(
            _payload(client_record.id, title="Low", priority="low"), user
        )

        rows, _ = await service.list_deals(
            filters=DealFilters(priority="low"), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Low"]

    async def test_value_range(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        await service.create_deal(
            _payload(client_record.id, title="Small", value=Decimal("500000")), user
        )
        await service.create_deal(
            _payload(client_record.id, title="Large", value=Decimal("4000000")), user
        )

        rows, _ = await service.list_deals(
            filters=DealFilters(min_value=Decimal("1000000")), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Large"]

    async def test_incoherent_value_range_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_value cannot exceed max_value"):
            DealFilters(min_value=Decimal("2"), max_value=Decimal("1"))

    async def test_search_matches_the_title(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        await service.create_deal(
            _payload(client_record.id, title="Sanchez purchase"), user
        )
        await service.create_deal(
            _payload(client_record.id, title="Divisadero sale"), user
        )
        await db.flush()

        rows, _ = await service.list_deals(
            filters=DealFilters(search="Sanchez"), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Sanchez purchase"]

    async def test_search_accepts_punctuation(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        await service.create_deal(_payload(client_record.id), user)
        await db.flush()

        for term in ["sanchez &", "!!!", "a | b", '"unclosed']:
            rows, _ = await service.list_deals(
                filters=DealFilters(search=term), limit=10, cursor=None
            )
            assert isinstance(rows, list)


class TestPagination:
    async def test_pages_do_not_overlap_or_skip(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        for index in range(12):
            await service.create_deal(
                _payload(client_record.id, title=f"Deal{index:02d}"), user
            )

        seen: list[str] = []
        cursor = None
        for _ in range(5):
            rows, has_more = await service.list_deals(
                filters=DealFilters(), limit=5, cursor=cursor
            )
            seen.extend(r.title for r in rows)
            if not has_more or not rows:
                break
            cursor = Cursor(created_at=rows[-1].created_at, id=rows[-1].id)

        assert len(seen) == 12
        assert len(set(seen)) == 12, "a row appeared on two pages"


class TestUpdateAndDelete:
    async def test_partial_update(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        updated = await service.update_deal(
            deal.id, DealUpdate(priority="urgent"), user
        )
        assert updated.priority == "urgent"
        assert updated.title == deal.title

    async def test_soft_delete_hides_but_retains_history(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """The analytics substrate outlives the record."""
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)

        await service.delete_deal(deal.id, user)

        with pytest.raises(NotFoundError):
            await service.get_deal(deal.id)

        history = (
            await db.execute(
                select(func.count())
                .select_from(DealStageHistory)
                .where(DealStageHistory.deal_id == deal.id)
            )
        ).scalar()
        assert history == 1

    async def test_deleting_twice_is_a_404(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_payload(client_record.id), user)
        await service.delete_deal(deal.id, user)

        with pytest.raises(NotFoundError):
            await service.delete_deal(deal.id, user)


class TestAudit:
    async def test_create_is_audited(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        deal = await DealService(db, auth).create_deal(
            _payload(client_record.id), user
        )

        entry = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.action == AuditAction.RECORD_CREATED)
                .where(AuditLog.entity_type == "deal")
            )
        ).scalar_one()
        assert entry.entity_id == deal.id

    async def test_no_op_update_is_not_audited(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(
            _payload(client_record.id, priority="high"), user
        )

        await service.update_deal(deal.id, DealUpdate(priority="high"), user)

        entries = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_UPDATED)
            )
        ).scalars().all()
        assert entries == []
