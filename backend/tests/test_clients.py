"""Clients — the second vertical slice.

Mirrors tests/test_leads.py, because the properties that matter are the same
ones and they must be proven independently for every entity: scope is a WHERE
clause, out-of-scope is 404, pagination is stable, mutations are audited.

Two classes are specific to clients:

  * `TestIdentity` — a client may be a person or a company, and must be one of
    them. That rule lives in three places (Pydantic, the service, a database
    CHECK) and each is load-bearing.
  * `TestAssignPermission` — `contacts.assign` is new in this slice and needs
    the same proof `leads.assign` has.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.audit import AuditLog
from app.models.client import Client
from app.models.organization import Organization
from app.models.rbac import Team, TeamMember
from app.schemas.client import ClientCreate, ClientFilters, ClientUpdate
from app.schemas.common import Cursor
from app.services.client import ClientService
from app.services.rbac import AuthorizationContext
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


def _payload(**overrides: object) -> ClientCreate:
    data: dict = {
        "first_name": "Omar",
        "last_name": "Haddad",
        "email": "omar@example.com",
        "type": "seller",
        "status": "active",
        **overrides,
    }
    return ClientCreate(**data)


# --------------------------------------------------------------------- tests


class TestCreate:
    async def test_creates_a_client_owned_by_the_creator(
        self, db: AsyncSession, agent
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = agent
        client = await ClientService(db, auth).create_client(_payload(), user)

        assert client.owner_id == user.id
        assert client.organization_id == user.organization_id
        assert client.display_name == "Omar Haddad"
        assert client.created_by == user.id

    async def test_requires_the_manage_permission(
        self, db: AsyncSession, organization: Organization, rbac_seeded: None
    ) -> None:
        """A viewer must not be able to create."""
        user = await make_user(db, organization, "viewer@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("viewer",),
            grants={"contacts.view": Scope.ALL},
        )
        with pytest.raises(PermissionDeniedError):
            await ClientService(db, auth).create_client(_payload(), user)

    async def test_agent_cannot_assign_to_someone_else(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        """An agent holds no contacts.assign at all."""
        user, auth = agent
        target, _ = other_agent

        with pytest.raises(PermissionDeniedError):
            await ClientService(db, auth).create_client(
                _payload(owner_id=target.id), user
            )

    async def test_admin_can_assign_to_anyone_in_the_workspace(
        self, db: AsyncSession, admin, agent
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        target, _ = agent

        client = await ClientService(db, auth).create_client(
            _payload(owner_id=target.id), user
        )
        assert client.owner_id == target.id

    async def test_cannot_assign_to_a_user_from_another_tenant(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """Otherwise the client is written with an owner RLS can never match —
        silently invisible to everyone rather than leaked, but lost."""
        user, auth = admin
        outsider = await make_user(db, other_organization, "outsider@meridian.example")

        with pytest.raises(NotFoundError):
            await ClientService(db, auth).create_client(
                _payload(owner_id=outsider.id), user
            )

    async def test_negative_lifetime_value_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            _payload(lifetime_value=Decimal("-1"))


class TestIdentity:
    """A client is a person or a company, and must be one of them."""

    async def test_company_only_client_is_valid(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        client = await ClientService(db, auth).create_client(
            ClientCreate(
                company_name="Tanaka Holdings Co", type="investor"
            ),
            user,
        )
        assert client.display_name == "Tanaka Holdings Co"
        assert client.is_company is True

    async def test_company_name_wins_over_person_name(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A person at a company is filed under the company."""
        user, auth = admin
        client = await ClientService(db, auth).create_client(
            _payload(company_name="Meridian Capital"), user
        )
        assert client.display_name == "Meridian Capital"

    async def test_no_identity_at_all_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="first and last name, or a company name"):
            ClientCreate(email="nobody@example.com")

    async def test_half_a_person_name_is_rejected(self) -> None:
        """A first name alone is not an identity."""
        with pytest.raises(ValueError, match="first and last name, or a company name"):
            ClientCreate(first_name="Omar")

    async def test_patch_cannot_empty_the_last_identity(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Checked against the merged record, not the payload alone.

        Clearing `company_name` is fine when a person name remains, and not
        fine when it is the only identity the record has.
        """
        user, auth = admin
        service = ClientService(db, auth)
        company = await service.create_client(
            ClientCreate(company_name="Solo Corp"), user
        )

        with pytest.raises(ConflictError):
            await service.update_client(
                company.id, ClientUpdate(company_name=None), user
            )

    async def test_patch_may_clear_company_when_a_person_name_remains(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        client = await service.create_client(
            _payload(company_name="Meridian Capital"), user
        )

        updated = await service.update_client(
            client.id, ClientUpdate(company_name=None), user
        )
        assert updated.display_name == "Omar Haddad"

    async def test_database_rejects_an_identity_less_row(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """The CHECK is the backstop when the service layer is bypassed."""
        from sqlalchemy.exc import IntegrityError

        db.add(Client(organization_id=organization.id, type="buyer", status="active"))
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


class TestScopeIsAWhereClause:
    """The central RBAC property for CRM data."""

    async def test_agent_sees_only_their_own_clients(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent

        await ClientService(db, mine_auth).create_client(
            _payload(first_name="Mine"), mine_user
        )
        await ClientService(db, theirs_auth).create_client(
            _payload(first_name="Theirs"), theirs_user
        )

        rows, _ = await ClientService(db, mine_auth).list_clients(
            filters=ClientFilters(), limit=50, cursor=None
        )
        assert [r.first_name for r in rows] == ["Mine"]

    async def test_admin_sees_every_client(
        self, db: AsyncSession, admin, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        _, admin_auth = admin
        a_user, a_auth = agent
        b_user, b_auth = other_agent

        await ClientService(db, a_auth).create_client(_payload(first_name="A"), a_user)
        await ClientService(db, b_auth).create_client(_payload(first_name="B"), b_user)

        rows, _ = await ClientService(db, admin_auth).list_clients(
            filters=ClientFilters(), limit=50, cursor=None
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

        await ClientService(db, member_auth).create_client(
            _payload(first_name="TeamClient"), member
        )
        await ClientService(db, outsider_auth).create_client(
            _payload(first_name="OutsideTeam"), outsider
        )

        rows, _ = await ClientService(db, manager_auth).list_clients(
            filters=ClientFilters(), limit=50, cursor=None
        )
        names = {r.first_name for r in rows}
        assert "TeamClient" in names
        assert "OutsideTeam" not in names

    async def test_out_of_scope_client_is_404_not_403(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        """403 would confirm the record exists — an existence oracle."""
        _, mine_auth = agent
        theirs_user, theirs_auth = other_agent

        theirs = await ClientService(db, theirs_auth).create_client(
            _payload(), theirs_user
        )

        with pytest.raises(NotFoundError):
            await ClientService(db, mine_auth).get_client(theirs.id)

    async def test_cannot_update_a_client_outside_scope(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        _, mine_auth = agent
        theirs_user, theirs_auth = other_agent
        theirs = await ClientService(db, theirs_auth).create_client(
            _payload(), theirs_user
        )

        with pytest.raises(NotFoundError):
            await ClientService(db, mine_auth).update_client(
                theirs.id, ClientUpdate(status="dormant"), theirs_user
            )

    async def test_type_counts_respect_scope(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent

        await ClientService(db, mine_auth).create_client(
            _payload(type="buyer"), mine_user
        )
        for _ in range(3):
            await ClientService(db, theirs_auth).create_client(
                _payload(type="buyer"), theirs_user
            )

        assert await ClientService(db, mine_auth).type_counts() == {"buyer": 1}


class TestAssignPermission:
    """`contacts.assign` is new in this slice."""

    async def test_agent_cannot_assign_at_all(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = agent
        target, _ = other_agent
        client = await ClientService(db, auth).create_client(_payload(), user)

        with pytest.raises(PermissionDeniedError):
            await ClientService(db, auth).assign_client(client.id, target.id, user)

    async def test_admin_can_reassign(
        self, db: AsyncSession, admin, agent
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        target, _ = agent
        client = await ClientService(db, auth).create_client(_payload(), user)

        reassigned = await ClientService(db, auth).assign_client(
            client.id, target.id, user
        )
        assert reassigned.owner_id == target.id

    async def test_manager_cannot_assign_outside_the_team(
        self, db: AsyncSession, organization: Organization, rbac_seeded: None
    ) -> None:
        """TEAM scope on contacts.assign means teammates only."""
        manager = await make_user(db, organization, "manager@vantage.example")
        outsider = await make_user(db, organization, "solo@vantage.example")

        team = Team(organization_id=organization.id, name="Westside")
        db.add(team)
        await db.flush()
        db.add(
            TeamMember(
                team_id=team.id,
                user_id=manager.id,
                organization_id=organization.id,
            )
        )
        await db.flush()

        manager_auth = await auth_for(db, manager, "manager")
        service = ClientService(db, manager_auth)
        client = await service.create_client(_payload(), manager)

        with pytest.raises(PermissionDeniedError):
            await service.assign_client(client.id, outsider.id, manager)

    async def test_cannot_assign_to_another_tenant(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        outsider = await make_user(db, other_organization, "outsider@meridian.example")
        client = await ClientService(db, auth).create_client(_payload(), user)

        with pytest.raises(NotFoundError):
            await ClientService(db, auth).assign_client(client.id, outsider.id, user)


class TestTenantIsolation:
    async def test_clients_never_cross_organizations(
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
            grants={"contacts.view": Scope.ALL, "contacts.manage": Scope.ALL},
        )

        await ClientService(db, outsider_auth).create_client(
            _payload(first_name="Foreign"), outsider
        )

        rows, _ = await ClientService(db, admin_auth).list_clients(
            filters=ClientFilters(), limit=50, cursor=None
        )
        assert all(r.first_name != "Foreign" for r in rows)

    async def test_rls_blocks_an_unscoped_query(
        self, db: AsyncSession, admin, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """RLS must hold even when the repository predicate is bypassed."""
        user, auth = admin
        await ClientService(db, auth).create_client(_payload(), user)
        await db.commit()

        from app.db.sql_objects import tenant_policy_statements

        for statement in tenant_policy_statements("clients"):
            await db.execute(text(statement))
        await db.commit()

        try:
            async with db.begin():
                # No organization predicate at all.
                rows = (await db.execute(select(Client))).unique().scalars().all()
            assert rows == [], (
                "An unscoped query returned rows with no tenant context bound. "
                "RLS is not enforcing on clients."
            )
        finally:
            await db.execute(text("DROP POLICY IF EXISTS tenant_isolation ON clients"))
            await db.execute(text("ALTER TABLE clients NO FORCE ROW LEVEL SECURITY"))
            await db.execute(text("ALTER TABLE clients DISABLE ROW LEVEL SECURITY"))
            await db.commit()


class TestPagination:
    async def test_pages_do_not_overlap_or_skip(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        for index in range(12):
            await service.create_client(_payload(first_name=f"Client{index:02d}"), user)

        seen: list[str] = []
        cursor = None
        for _ in range(5):
            rows, has_more = await service.list_clients(
                filters=ClientFilters(), limit=5, cursor=cursor
            )
            seen.extend(r.first_name for r in rows)
            if not has_more or not rows:
                break
            cursor = Cursor(created_at=rows[-1].created_at, id=rows[-1].id)

        assert len(seen) == 12
        assert len(set(seen)) == 12, "a row appeared on two pages"

    async def test_has_more_is_accurate(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        for index in range(3):
            await service.create_client(_payload(first_name=f"C{index}"), user)

        _, has_more = await service.list_clients(
            filters=ClientFilters(), limit=2, cursor=None
        )
        assert has_more is True

        _, has_more = await service.list_clients(
            filters=ClientFilters(), limit=10, cursor=None
        )
        assert has_more is False


class TestFiltersAndSearch:
    async def test_filter_by_type(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        await service.create_client(_payload(first_name="Buyer", type="buyer"), user)
        await service.create_client(
            _payload(first_name="Investor", type="investor"), user
        )

        rows, _ = await service.list_clients(
            filters=ClientFilters(type="investor"), limit=50, cursor=None
        )
        assert [r.first_name for r in rows] == ["Investor"]

    async def test_filter_by_status(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        await service.create_client(_payload(first_name="Active"), user)
        await service.create_client(
            _payload(first_name="Dormant", status="dormant"), user
        )

        rows, _ = await service.list_clients(
            filters=ClientFilters(status="dormant"), limit=50, cursor=None
        )
        assert [r.first_name for r in rows] == ["Dormant"]

    async def test_filter_by_tag(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        await service.create_client(
            _payload(first_name="Tagged", tags=["vip", "repeat"]), user
        )
        await service.create_client(_payload(first_name="Untagged"), user)

        rows, _ = await service.list_clients(
            filters=ClientFilters(tag="vip"), limit=50, cursor=None
        )
        assert [r.first_name for r in rows] == ["Tagged"]

    async def test_search_matches_a_person_name(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        await service.create_client(
            _payload(first_name="Harper", last_name="Lindqvist"), user
        )
        await service.create_client(
            _payload(first_name="Theo", last_name="Bergstrom"), user
        )
        await db.flush()

        rows, _ = await service.list_clients(
            filters=ClientFilters(search="Lindqvist"), limit=50, cursor=None
        )
        assert [r.last_name for r in rows] == ["Lindqvist"]

    async def test_search_matches_a_company_name(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The reason company_name is in the generated search_vector."""
        user, auth = admin
        service = ClientService(db, auth)
        await service.create_client(
            ClientCreate(company_name="Tanaka Holdings Co", type="investor"), user
        )
        await service.create_client(_payload(first_name="Someone"), user)
        await db.flush()

        rows, _ = await service.list_clients(
            filters=ClientFilters(search="Tanaka"), limit=50, cursor=None
        )
        assert [r.company_name for r in rows] == ["Tanaka Holdings Co"]

    async def test_search_accepts_punctuation(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """websearch_to_tsquery tolerates what users actually type."""
        user, auth = admin
        service = ClientService(db, auth)
        await service.create_client(_payload(), user)
        await db.flush()

        for term in ["omar &", "!!!", "a | b", '"unclosed']:
            rows, _ = await service.list_clients(
                filters=ClientFilters(search=term), limit=10, cursor=None
            )
            assert isinstance(rows, list)

    async def test_search_respects_scope(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        """Search must not become a way around RBAC."""
        _mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent

        await ClientService(db, theirs_auth).create_client(
            _payload(first_name="Secret", last_name="Principal"), theirs_user
        )
        await db.flush()

        rows, _ = await ClientService(db, mine_auth).list_clients(
            filters=ClientFilters(search="Secret"), limit=50, cursor=None
        )
        assert rows == []


class TestUpdateAndDelete:
    async def test_partial_update_touches_only_given_fields(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        client = await service.create_client(_payload(notes="original note"), user)

        updated = await service.update_client(
            client.id, ClientUpdate(status="dormant"), user
        )

        assert updated.status == "dormant"
        assert updated.notes == "original note"

    async def test_server_generated_columns_are_readable_after_update(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Every field the response model reads must be available without IO.

        `updated_at` has a server-side onupdate and `search_vector` is
        generated, so both are expired after an UPDATE; reading them while
        serialising would raise MissingGreenlet — a 500 on every PATCH.
        """
        user, auth = admin
        service = ClientService(db, auth)
        client = await service.create_client(_payload(), user)

        updated = await service.update_client(
            client.id, ClientUpdate(status="dormant"), user
        )

        assert updated.updated_at is not None
        assert updated.created_at is not None
        assert updated.display_name
        assert updated.status == "dormant"

    async def test_soft_delete_hides_but_retains(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        client = await service.create_client(_payload(), user)

        await service.delete_client(client.id, user)

        with pytest.raises(NotFoundError):
            await service.get_client(client.id)

        # The row survives for retention and audit.
        row = (
            await db.execute(select(Client).where(Client.id == client.id))
        ).unique().scalar_one()
        assert row.deleted_at is not None

    async def test_deleting_twice_is_a_404(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        client = await service.create_client(_payload(), user)
        await service.delete_client(client.id, user)

        with pytest.raises(NotFoundError):
            await service.delete_client(client.id, user)


class TestAudit:
    async def test_create_is_audited(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        client = await ClientService(db, auth).create_client(_payload(), user)

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_CREATED)
            )
        ).scalar_one()
        assert entry.entity_type == "client"
        assert entry.entity_id == client.id
        assert entry.actor_id == user.id

    async def test_update_records_a_field_diff(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        client = await service.create_client(_payload(status="active"), user)

        await service.update_client(client.id, ClientUpdate(status="dormant"), user)

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_UPDATED)
            )
        ).scalar_one()
        assert entry.metadata_["changes"]["status"] == {
            "old": "active",
            "new": "dormant",
        }

    async def test_no_op_update_is_not_audited(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Setting a field to its current value is not an event."""
        user, auth = admin
        service = ClientService(db, auth)
        client = await service.create_client(_payload(status="active"), user)

        await service.update_client(client.id, ClientUpdate(status="active"), user)

        entries = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_UPDATED)
            )
        ).scalars().all()
        assert entries == []

    async def test_delete_is_audited(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        client = await service.create_client(_payload(), user)
        await service.delete_client(client.id, user)

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_DELETED)
            )
        ).scalar_one()
        assert entry.entity_id == client.id


class TestCursor:
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
