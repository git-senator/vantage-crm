"""Leads — the reference vertical slice.

Four properties are load-bearing and each has its own class:

  * RBAC scope becomes a WHERE clause, so an agent's list simply does not
    contain other people's leads;
  * a lead outside scope is a 404, never a 403, so there is no existence
    oracle;
  * keyset pagination is stable under concurrent inserts, which OFFSET is not;
  * every mutation leaves an audit entry.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.audit import AuditLog
from app.models.lead import Lead
from app.models.organization import Organization
from app.models.rbac import Team, TeamMember
from app.schemas.common import Cursor
from app.schemas.lead import LeadCreate, LeadFilters, LeadUpdate
from app.services.lead import LeadService
from app.services.rbac import AuthorizationContext
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


# ------------------------------------------------------------------ fixtures


def _payload(**overrides: object) -> LeadCreate:
    data: dict = {
        "first_name": "Harper",
        "last_name": "Lindqvist",
        "email": "harper@example.com",
        "stage": "new",
        "source": "zillow",
        "temperature": "hot",
        **overrides,
    }
    return LeadCreate(**data)


# --------------------------------------------------------------------- tests


class TestCreate:
    async def test_creates_a_lead_owned_by_the_creator(self, db: AsyncSession, agent) -> None:  # type: ignore[no-untyped-def]
        user, auth = agent
        lead = await LeadService(db, auth).create_lead(_payload(), user)

        assert lead.owner_id == user.id
        assert lead.organization_id == user.organization_id
        assert lead.full_name == "Harper Lindqvist"
        assert lead.created_by == user.id

    async def test_requires_the_manage_permission(
        self, db: AsyncSession, organization: Organization, rbac_seeded: None
    ) -> None:
        """A viewer must not be able to create."""
        user = await make_user(db, organization, "viewer@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("viewer",),
            grants={"leads.view": Scope.ALL},
        )
        with pytest.raises(PermissionDeniedError):
            await LeadService(db, auth).create_lead(_payload(), user)

    async def test_agent_cannot_assign_to_someone_else(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        """OWN scope on leads.assign means "yourself only"."""
        user, auth = agent
        target, _ = other_agent

        with pytest.raises(PermissionDeniedError):
            await LeadService(db, auth).create_lead(
                _payload(owner_id=target.id), user
            )

    async def test_admin_can_assign_to_anyone_in_the_workspace(
        self, db: AsyncSession, admin, agent
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        target, _ = agent

        lead = await LeadService(db, auth).create_lead(
            _payload(owner_id=target.id), user
        )
        assert lead.owner_id == target.id

    async def test_cannot_assign_to_a_user_from_another_tenant(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """Otherwise the lead is written with an owner RLS can never match —
        silently invisible to everyone rather than leaked, but lost."""
        user, auth = admin
        outsider = await make_user(db, other_organization, "outsider@meridian.example")

        with pytest.raises(NotFoundError):
            await LeadService(db, auth).create_lead(
                _payload(owner_id=outsider.id), user
            )

    async def test_budget_range_is_validated(self) -> None:
        with pytest.raises(ValueError, match="budget_min cannot exceed budget_max"):
            _payload(budget_min=Decimal("900000"), budget_max=Decimal("500000"))


class TestScopeIsAWhereClause:
    """The central RBAC property for CRM data."""

    async def test_agent_sees_only_their_own_leads(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent

        await LeadService(db, mine_auth).create_lead(
            _payload(first_name="Mine"), mine_user
        )
        await LeadService(db, theirs_auth).create_lead(
            _payload(first_name="Theirs"), theirs_user
        )

        rows, _ = await LeadService(db, mine_auth).list_leads(
            filters=LeadFilters(), limit=50, cursor=None
        )

        assert [r.first_name for r in rows] == ["Mine"]

    async def test_admin_sees_every_lead(
        self, db: AsyncSession, admin, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        _, admin_auth = admin
        a_user, a_auth = agent
        b_user, b_auth = other_agent

        await LeadService(db, a_auth).create_lead(_payload(first_name="A"), a_user)
        await LeadService(db, b_auth).create_lead(_payload(first_name="B"), b_user)

        rows, _ = await LeadService(db, admin_auth).list_leads(
            filters=LeadFilters(), limit=50, cursor=None
        )
        assert {r.first_name for r in rows} == {"A", "B"}

    async def test_manager_sees_the_team(
        self, db: AsyncSession, organization: Organization, rbac_seeded: None
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

        await LeadService(db, member_auth).create_lead(
            _payload(first_name="TeamLead"), member
        )
        await LeadService(db, outsider_auth).create_lead(
            _payload(first_name="OutsideTeam"), outsider
        )

        rows, _ = await LeadService(db, manager_auth).list_leads(
            filters=LeadFilters(), limit=50, cursor=None
        )
        names = {r.first_name for r in rows}
        assert "TeamLead" in names
        assert "OutsideTeam" not in names

    async def test_out_of_scope_lead_is_404_not_403(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        """403 would confirm the record exists — an existence oracle."""
        _, mine_auth = agent
        theirs_user, theirs_auth = other_agent

        theirs = await LeadService(db, theirs_auth).create_lead(
            _payload(), theirs_user
        )

        with pytest.raises(NotFoundError):
            await LeadService(db, mine_auth).get_lead(theirs.id)

    async def test_cannot_update_a_lead_outside_scope(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        _, mine_auth = agent
        theirs_user, theirs_auth = other_agent
        theirs = await LeadService(db, theirs_auth).create_lead(
            _payload(), theirs_user
        )

        with pytest.raises(NotFoundError):
            await LeadService(db, mine_auth).update_lead(
                theirs.id, LeadUpdate(stage="qualified"), theirs_user
            )

    async def test_stage_counts_respect_scope(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent

        await LeadService(db, mine_auth).create_lead(
            _payload(stage="new"), mine_user
        )
        for _ in range(3):
            await LeadService(db, theirs_auth).create_lead(
                _payload(stage="new"), theirs_user
            )

        assert await LeadService(db, mine_auth).stage_counts() == {"new": 1}


class TestTenantIsolation:
    async def test_leads_never_cross_organizations(
        self,
        db: AsyncSession,
        admin,
        other_organization: Organization,
        rbac_seeded: None,
    ) -> None:  # type: ignore[no-untyped-def]
        """Even an admin with ALL scope is confined to their own tenant."""
        _, admin_auth = admin
        outsider = await make_user(db, other_organization, "outsider@meridian.example")
        outsider_auth = AuthorizationContext(
            user_id=outsider.id,
            organization_id=other_organization.id,
            role_keys=("admin",),
            grants={"leads.view": Scope.ALL, "leads.manage": Scope.ALL},
        )

        await LeadService(db, outsider_auth).create_lead(
            _payload(first_name="Foreign"), outsider
        )

        rows, _ = await LeadService(db, admin_auth).list_leads(
            filters=LeadFilters(), limit=50, cursor=None
        )
        assert all(r.first_name != "Foreign" for r in rows)

    async def test_rls_blocks_an_unscoped_query(
        self, db: AsyncSession, admin, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """RLS must hold even when the repository predicate is bypassed."""
        user, auth = admin
        await LeadService(db, auth).create_lead(_payload(), user)
        await db.commit()

        from app.db.sql_objects import tenant_policy_statements

        for statement in tenant_policy_statements("leads"):
            await db.execute(text(statement))
        await db.commit()

        try:
            async with db.begin():
                # No organization predicate at all.
                rows = (await db.execute(select(Lead))).unique().scalars().all()
            assert rows == [], (
                "An unscoped query returned rows with no tenant context bound. "
                "RLS is not enforcing on leads."
            )
        finally:
            await db.execute(text("DROP POLICY IF EXISTS tenant_isolation ON leads"))
            await db.execute(text("ALTER TABLE leads NO FORCE ROW LEVEL SECURITY"))
            await db.execute(text("ALTER TABLE leads DISABLE ROW LEVEL SECURITY"))
            await db.commit()


class TestPagination:
    async def test_pages_do_not_overlap_or_skip(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = LeadService(db, auth)
        for index in range(12):
            await service.create_lead(_payload(first_name=f"Lead{index:02d}"), user)

        seen: list[str] = []
        cursor = None
        for _ in range(5):
            rows, has_more = await service.list_leads(
                filters=LeadFilters(), limit=5, cursor=cursor
            )
            seen.extend(r.first_name for r in rows)
            if not has_more or not rows:
                break
            cursor = Cursor(created_at=rows[-1].created_at, id=rows[-1].id)

        assert len(seen) == 12
        assert len(set(seen)) == 12, "a row appeared on two pages"

    async def test_has_more_is_accurate(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = LeadService(db, auth)
        for index in range(3):
            await service.create_lead(_payload(first_name=f"L{index}"), user)

        _, has_more = await service.list_leads(
            filters=LeadFilters(), limit=2, cursor=None
        )
        assert has_more is True

        _, has_more = await service.list_leads(
            filters=LeadFilters(), limit=10, cursor=None
        )
        assert has_more is False

    async def test_malformed_cursor_is_ignored(self) -> None:
        """Cursors end up in bookmarks; a stale one must degrade gracefully."""
        assert Cursor.decode("not-a-real-cursor") is None
        assert Cursor.decode("") is None

    async def test_cursor_round_trips(self) -> None:
        from datetime import UTC, datetime

        original = Cursor(created_at=datetime.now(UTC), id=uuid.uuid4())
        decoded = Cursor.decode(original.encode())

        assert decoded is not None
        assert decoded.id == original.id


class TestFiltersAndSearch:
    async def test_filter_by_stage(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = LeadService(db, auth)
        await service.create_lead(_payload(first_name="New", stage="new"), user)
        await service.create_lead(
            _payload(first_name="Qualified", stage="qualified"), user
        )

        rows, _ = await service.list_leads(
            filters=LeadFilters(stage="qualified"), limit=50, cursor=None
        )
        assert [r.first_name for r in rows] == ["Qualified"]

    async def test_filter_by_tag(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = LeadService(db, auth)
        await service.create_lead(
            _payload(first_name="Tagged", tags=["pre-approved", "urgent"]), user
        )
        await service.create_lead(_payload(first_name="Untagged"), user)

        rows, _ = await service.list_leads(
            filters=LeadFilters(tag="urgent"), limit=50, cursor=None
        )
        assert [r.first_name for r in rows] == ["Tagged"]

    async def test_full_text_search_matches_a_name(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = LeadService(db, auth)
        await service.create_lead(
            _payload(first_name="Harper", last_name="Lindqvist"), user
        )
        await service.create_lead(
            _payload(first_name="Theo", last_name="Bergstrom"), user
        )
        await db.flush()

        rows, _ = await service.list_leads(
            filters=LeadFilters(search="Lindqvist"), limit=50, cursor=None
        )
        assert [r.last_name for r in rows] == ["Lindqvist"]

    async def test_search_matches_a_location(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = LeadService(db, auth)
        await service.create_lead(
            _payload(first_name="A", preferred_location="Noe Valley"), user
        )
        await service.create_lead(
            _payload(first_name="B", preferred_location="Pacific Heights"), user
        )
        await db.flush()

        rows, _ = await service.list_leads(
            filters=LeadFilters(search="Noe"), limit=50, cursor=None
        )
        assert [r.first_name for r in rows] == ["A"]

    async def test_search_accepts_punctuation(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """websearch_to_tsquery tolerates what users actually type.

        `to_tsquery` raises a syntax error on input like "harper & " — which is
        a 500 on a search box.
        """
        user, auth = admin
        service = LeadService(db, auth)
        await service.create_lead(_payload(), user)
        await db.flush()

        for term in ["harper &", "!!!", "a | b", '"unclosed']:
            rows, _ = await service.list_leads(
                filters=LeadFilters(search=term), limit=10, cursor=None
            )
            assert isinstance(rows, list)

    async def test_search_respects_scope(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        """Search must not become a way around RBAC."""
        _mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent

        await LeadService(db, theirs_auth).create_lead(
            _payload(first_name="Secret", last_name="Prospect"), theirs_user
        )
        await db.flush()

        rows, _ = await LeadService(db, mine_auth).list_leads(
            filters=LeadFilters(search="Secret"), limit=50, cursor=None
        )
        assert rows == []


class TestUpdateAndDelete:
    async def test_partial_update_touches_only_given_fields(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = LeadService(db, auth)
        lead = await service.create_lead(_payload(notes="original note"), user)

        updated = await service.update_lead(
            lead.id, LeadUpdate(stage="qualified"), user
        )

        assert updated.stage == "qualified"
        assert updated.notes == "original note"

    async def test_server_generated_columns_are_readable_after_update(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Regression: PATCH returned 500 in the real stack.

        `updated_at` has a server-side onupdate, so SQLAlchemy expires it after
        the UPDATE. Reading it while serialising the response triggered a lazy
        load outside the async context — MissingGreenlet, a 500 on every PATCH.

        The original tests missed it because none read `updated_at` after an
        update, which is exactly what the response model does.
        """
        user, auth = admin
        service = LeadService(db, auth)
        lead = await service.create_lead(_payload(), user)

        updated = await service.update_lead(
            lead.id, LeadUpdate(stage="qualified"), user
        )

        # Every field the response model reads must be available without IO.
        assert updated.updated_at is not None
        assert updated.created_at is not None
        assert updated.stage == "qualified"
        assert updated.full_name

    async def test_soft_delete_hides_but_retains(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = LeadService(db, auth)
        lead = await service.create_lead(_payload(), user)

        await service.delete_lead(lead.id, user)

        with pytest.raises(NotFoundError):
            await service.get_lead(lead.id)

        # The row survives for retention and audit.
        row = (
            await db.execute(select(Lead).where(Lead.id == lead.id))
        ).unique().scalar_one()
        assert row.deleted_at is not None

    async def test_deleting_twice_is_a_404(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = LeadService(db, auth)
        lead = await service.create_lead(_payload(), user)
        await service.delete_lead(lead.id, user)

        with pytest.raises(NotFoundError):
            await service.delete_lead(lead.id, user)


class TestAudit:
    async def test_create_is_audited(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(_payload(), user)

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_CREATED)
            )
        ).scalar_one()
        assert entry.entity_type == "lead"
        assert entry.entity_id == lead.id
        assert entry.actor_id == user.id

    async def test_update_records_a_field_diff(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = LeadService(db, auth)
        lead = await service.create_lead(_payload(stage="new"), user)

        await service.update_lead(lead.id, LeadUpdate(stage="qualified"), user)

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_UPDATED)
            )
        ).scalar_one()
        assert entry.metadata_["changes"]["stage"] == {
            "old": "new",
            "new": "qualified",
        }

    async def test_no_op_update_is_not_audited(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Setting a field to its current value is not an event."""
        user, auth = admin
        service = LeadService(db, auth)
        lead = await service.create_lead(_payload(stage="new"), user)

        await service.update_lead(lead.id, LeadUpdate(stage="new"), user)

        entries = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_UPDATED)
            )
        ).scalars().all()
        assert entries == []

    async def test_delete_is_audited(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = LeadService(db, auth)
        lead = await service.create_lead(_payload(), user)
        await service.delete_lead(lead.id, user)

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_DELETED)
            )
        ).scalar_one()
        assert entry.entity_id == lead.id
