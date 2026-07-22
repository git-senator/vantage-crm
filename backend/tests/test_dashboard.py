"""Dashboard — scoped aggregates over the CRM.

The property that matters most here is that **every number is the caller's own
scope**. The dashboard does not invent a wider view than the list endpoints
grant: an agent's counts are their book, a manager's are the team's, and an
entity the caller cannot see contributes zero rather than raising.

The rest pins the derivations: open vs. won deals come from the stage (never a
column on the deal), weighted value is value x probability, and overdue tasks
are a predicate on the clock, not a stored flag.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.schemas.client import ClientCreate
from app.schemas.deal import DealCreate, DealStageTransition
from app.schemas.lead import LeadCreate
from app.schemas.task import TaskCreate
from app.services.client import ClientService
from app.services.dashboard import DashboardService
from app.services.deal import DealService
from app.services.lead import LeadService
from app.services.pipeline import build_default_pipeline
from app.services.task import TaskService
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
        ClientCreate(first_name="Tomas", last_name="Vega", type="buyer"), user
    )


def _deal(client_id, **overrides):  # type: ignore[no-untyped-def]
    data: dict = {
        "title": "Deal",
        "client_id": client_id,
        "value": Decimal("500000.00"),
        **overrides,
    }
    return DealCreate(**data)


class TestCounts:
    async def test_counts_reflect_created_records(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="A", last_name="One"), user
        )
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="B", last_name="Two"), user
        )
        await DealService(db, auth).create_deal(_deal(client_record.id), user)
        await TaskService(db, auth).create_task(TaskCreate(title="Do a thing"), user)

        summary = await DashboardService(db, auth).summary()
        assert summary.leads.open == 2
        assert summary.leads.total == 2
        assert summary.clients.total == 1  # the fixture client
        assert summary.deals.open_count == 1
        assert summary.deals.open_value == Decimal("500000.00")
        assert summary.tasks.open == 1

    async def test_weighted_value_is_value_times_probability(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        # qualification's default probability is 10%.
        await DealService(db, auth).create_deal(_deal(client_record.id), user)
        summary = await DashboardService(db, auth).summary()
        assert summary.deals.weighted_value == Decimal("50000.00")

    async def test_overdue_tasks_are_counted(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        past = datetime.now(UTC) - timedelta(days=1)
        await TaskService(db, auth).create_task(
            TaskCreate(title="Late", due_at=past), user
        )
        await TaskService(db, auth).create_task(TaskCreate(title="No date"), user)
        summary = await DashboardService(db, auth).summary()
        assert summary.tasks.open == 2
        assert summary.tasks.overdue == 1


class TestWonThisMonth:
    async def test_winning_moves_the_deal_out_of_open_into_won(
        self, db: AsyncSession, admin, pipeline, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = DealService(db, auth)
        deal = await service.create_deal(_deal(client_record.id), user)
        await service.move_stage(
            deal.id,
            DealStageTransition(to_stage_id=stage_by_key(pipeline, "closed_won").id),
            user,
        )
        summary = await DashboardService(db, auth).summary()
        assert summary.deals.open_count == 0
        assert summary.deals.won_this_month_count == 1
        assert summary.deals.won_this_month_value == Decimal("500000.00")


class TestScope:
    async def test_agent_sees_only_their_own_numbers(
        self, db: AsyncSession, organization: Organization, rbac_seeded, pipeline
    ) -> None:  # type: ignore[no-untyped-def]
        one = await make_user(db, organization, "one@vantage.example")
        two = await make_user(db, organization, "two@vantage.example")
        one_auth = await auth_for(db, one, "agent")
        two_auth = await auth_for(db, two, "agent")

        await LeadService(db, one_auth).create_lead(
            LeadCreate(first_name="Ones", last_name="Lead"), one
        )
        await LeadService(db, two_auth).create_lead(
            LeadCreate(first_name="Twos", last_name="Lead"), two
        )

        one_summary = await DashboardService(db, one_auth).summary()
        assert one_summary.leads.total == 1

    async def test_recent_activity_is_populated(
        self, db: AsyncSession, admin, client_record
    ) -> None:  # type: ignore[no-untyped-def]
        """A note on a record surfaces in the dashboard's recent-activity feed —
        the same merged timeline, so the panel and the timeline agree."""
        from app.schemas.note import NoteCreate
        from app.services.note import NoteService

        user, auth = admin
        await NoteService(db, auth).create_note(
            NoteCreate(
                entity_type="client", entity_id=client_record.id, body="First contact"
            ),
            user,
        )
        summary = await DashboardService(db, auth).summary()
        assert len(summary.recent_activity) >= 1
        assert summary.recent_activity[0].kind == "note"


class TestTenantIsolation:
    async def test_other_orgs_records_are_not_counted(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        _user, admin_auth = admin
        outsider = await make_user(db, other_organization, "x@meridian.example")
        from app.core.permissions import Scope
        from app.services.rbac import AuthorizationContext

        outsider_auth = AuthorizationContext(
            user_id=outsider.id,
            organization_id=other_organization.id,
            role_keys=("admin",),
            grants={"leads.view": Scope.ALL, "leads.manage": Scope.ALL},
        )
        await LeadService(db, outsider_auth).create_lead(
            LeadCreate(first_name="Foreign", last_name="Lead"), outsider
        )

        summary = await DashboardService(db, admin_auth).summary()
        assert summary.leads.total == 0
