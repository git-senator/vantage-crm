"""Lead → Client conversion.

The one genuinely new domain action in this slice, and the one with a race in
it. Four properties are load-bearing:

  * conversion links both records and preserves the lead, because the lead is
    the top of the funnel and Phase 4 reporting is computed from it;
  * it is **one-shot** — proven under genuine concurrency, not just by a
    sequential second call;
  * it needs `leads.manage` AND `contacts.manage`, so neither permission alone
    is a way to create records the holder could not otherwise create;
  * it writes two audit entries, or none.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.audit import AuditLog
from app.models.client import Client
from app.models.lead import Lead
from app.models.organization import Organization
from app.schemas.client import ClientConvert
from app.schemas.lead import LeadCreate, LeadFilters
from app.services.client import ClientService
from app.services.lead import LeadService
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

pytestmark = pytest.mark.integration


def _lead_payload(**overrides: object) -> LeadCreate:
    data: dict = {
        "first_name": "Harper",
        "last_name": "Lindqvist",
        "email": "harper@example.com",
        "phone": "(415) 555-0142",
        "stage": "qualified",
        "source": "referral",
        "tags": ["pre-approved"],
        "notes": "Wants to close before September.",
        **overrides,
    }
    return LeadCreate(**data)


class TestConversion:
    async def test_links_both_records_and_keeps_the_lead(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(_lead_payload(), user)

        client, converted = await ClientService(db, auth).convert_lead(
            lead.id, ClientConvert(type="buyer"), user
        )

        assert client.source_lead_id == lead.id
        assert converted.converted_client_id == client.id
        assert converted.status == "converted"
        assert converted.converted_at is not None

    async def test_the_lead_survives_and_is_still_listable(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Deleting it would destroy the funnel data Phase 4 reports on."""
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(_lead_payload(), user)
        await ClientService(db, auth).convert_lead(
            lead.id, ClientConvert(), user
        )

        rows, _ = await LeadService(db, auth).list_leads(
            filters=LeadFilters(), limit=50, cursor=None
        )
        assert [r.id for r in rows] == [lead.id]

        # And it is filterable as converted, which is what the funnel counts.
        rows, _ = await LeadService(db, auth).list_leads(
            filters=LeadFilters(status="converted"), limit=50, cursor=None
        )
        assert [r.id for r in rows] == [lead.id]

    async def test_client_inherits_the_lead_details(
        self, db: AsyncSession, admin, agent
    ) -> None:  # type: ignore[no-untyped-def]
        """Whoever worked the lead keeps the relationship."""
        user, auth = admin
        target, _ = agent
        lead = await LeadService(db, auth).create_lead(
            _lead_payload(owner_id=target.id), user
        )

        client, _converted = await ClientService(db, auth).convert_lead(
            lead.id, ClientConvert(type="investor"), user
        )

        assert client.owner_id == target.id
        assert client.first_name == "Harper"
        assert client.last_name == "Lindqvist"
        assert client.email == "harper@example.com"
        assert client.phone == "(415) 555-0142"
        assert client.tags == ["pre-approved"]
        assert client.notes == "Wants to close before September."
        assert client.type == "investor"
        assert client.status == "active"
        assert client.client_since is not None

    async def test_a_company_name_may_be_supplied_at_conversion(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A lead is always a person; the client may be the entity they act for."""
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(_lead_payload(), user)

        client, _ = await ClientService(db, auth).convert_lead(
            lead.id, ClientConvert(type="investor", company_name="Lindqvist Trust"), user
        )

        assert client.display_name == "Lindqvist Trust"
        assert client.first_name == "Harper"

    async def test_converting_twice_is_a_conflict(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ClientService(db, auth)
        lead = await LeadService(db, auth).create_lead(_lead_payload(), user)

        await service.convert_lead(lead.id, ClientConvert(), user)

        with pytest.raises(ConflictError):
            await service.convert_lead(lead.id, ClientConvert(), user)

    async def test_converting_a_lead_outside_scope_is_404(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        """404, not 403 — no existence oracle, same as every other read."""
        _, mine_auth = agent
        theirs_user, theirs_auth = other_agent
        theirs = await LeadService(db, theirs_auth).create_lead(
            _lead_payload(), theirs_user
        )

        with pytest.raises(NotFoundError):
            await ClientService(db, mine_auth).convert_lead(
                theirs.id, ClientConvert(), theirs_user
            )

    async def test_cannot_convert_a_lead_from_another_tenant(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        outsider = await make_user(db, other_organization, "outsider@meridian.example")
        outsider_auth = AuthorizationContext(
            user_id=outsider.id,
            organization_id=other_organization.id,
            role_keys=("admin",),
            grants={"leads.manage": Scope.ALL, "leads.view": Scope.ALL},
        )
        foreign = await LeadService(db, outsider_auth).create_lead(
            _lead_payload(), outsider
        )

        with pytest.raises(NotFoundError):
            await ClientService(db, auth).convert_lead(
                foreign.id, ClientConvert(), user
            )


class TestConversionRequiresBothPermissions:
    """Either permission alone is a privilege escalation."""

    async def test_leads_manage_alone_is_insufficient(
        self, db: AsyncSession, admin, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """Otherwise a lead editor could mint client records."""
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(_lead_payload(), user)

        partial = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("partial",),
            grants={"leads.manage": Scope.ALL, "leads.view": Scope.ALL},
        )
        with pytest.raises(PermissionDeniedError):
            await ClientService(db, partial).convert_lead(
                lead.id, ClientConvert(), user
            )

    async def test_contacts_manage_alone_is_insufficient(
        self, db: AsyncSession, admin, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """Otherwise a client manager could convert leads they cannot see."""
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(_lead_payload(), user)

        partial = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("partial",),
            grants={"contacts.manage": Scope.ALL, "contacts.view": Scope.ALL},
        )
        with pytest.raises(PermissionDeniedError):
            await ClientService(db, partial).convert_lead(
                lead.id, ClientConvert(), user
            )

    async def test_nothing_is_written_when_authorization_fails(
        self, db: AsyncSession, admin, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(_lead_payload(), user)

        partial = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("partial",),
            grants={"leads.manage": Scope.ALL},
        )
        with pytest.raises(PermissionDeniedError):
            await ClientService(db, partial).convert_lead(
                lead.id, ClientConvert(), user
            )

        assert (await db.execute(select(func.count()).select_from(Client))).scalar() == 0
        refreshed = (
            await db.execute(select(Lead).where(Lead.id == lead.id))
        ).unique().scalar_one()
        assert refreshed.status == "open"
        assert refreshed.converted_client_id is None


class TestConcurrentConversion:
    """One lead, two simultaneous requests, on separate connections."""

    async def test_parallel_conversion_yields_exactly_one_client(
        self, engine, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Two simultaneous conversions produce one client and one 409.

        Scope of this test, stated precisely: it proves the **outcome**, not
        which mechanism delivered it. The lock and the unique index are
        redundant by design, and either alone would satisfy this assertion —
        without `FOR UPDATE` both transactions read `converted_client_id IS
        NULL`, both insert, and the loser is rejected by the index and
        surfaces the same ConflictError.

        That redundancy is deliberate: the index is the guarantee (see
        `test_the_unique_index_is_the_real_guarantee`), and the lock is what
        turns the loser's failure from an IntegrityError that poisons the
        transaction into a clean 409 from the service's own check.

        Each task needs its own session: concurrent work on one AsyncSession is
        unsupported and would prove nothing.
        """
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(_lead_payload(), user)
        # Committed so the parallel sessions can see the lead and the RBAC seed.
        await db.commit()

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)

        async def convert_once() -> str:
            async with factory() as session:
                try:
                    await ClientService(session, auth).convert_lead(
                        lead.id, ClientConvert(), user
                    )
                    await session.commit()
                    return "converted"
                except ConflictError:
                    await session.rollback()
                    return "conflict"

        results = await asyncio.gather(convert_once(), convert_once())

        assert sorted(results) == ["conflict", "converted"], (
            f"Expected exactly one conversion and one conflict, got {results}."
        )

        async with factory() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(Client)
                    .where(Client.source_lead_id == lead.id)
                )
            ).scalar()
            assert count == 1, f"{count} clients created from one lead."

    async def test_the_unique_index_is_the_real_guarantee(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Belt and braces: the index must reject a second row on its own.

        The service check and the row lock could both be removed by a future
        refactor. This asserts the database still refuses.
        """
        from sqlalchemy.exc import IntegrityError

        user, auth = admin
        lead = await LeadService(db, auth).create_lead(_lead_payload(), user)
        await ClientService(db, auth).convert_lead(lead.id, ClientConvert(), user)

        db.add(
            Client(
                organization_id=user.organization_id,
                first_name="Duplicate",
                last_name="Record",
                type="buyer",
                status="active",
                source_lead_id=lead.id,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()

    async def test_clients_without_a_source_lead_do_not_collide(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The index is partial for exactly this reason."""
        from app.schemas.client import ClientCreate

        user, auth = admin
        service = ClientService(db, auth)
        await service.create_client(
            ClientCreate(first_name="A", last_name="One"), user
        )
        await service.create_client(
            ClientCreate(first_name="B", last_name="Two"), user
        )

        count = (
            await db.execute(
                select(func.count())
                .select_from(Client)
                .where(Client.source_lead_id.is_(None))
            )
        ).scalar()
        assert count == 2


class TestConversionAudit:
    async def test_writes_both_entries(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        lead = await LeadService(db, auth).create_lead(_lead_payload(), user)
        client, _ = await ClientService(db, auth).convert_lead(
            lead.id, ClientConvert(), user
        )

        converted = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_CONVERTED)
            )
        ).scalar_one()
        assert converted.entity_type == "lead"
        assert converted.entity_id == lead.id
        assert converted.metadata_["client_id"] == str(client.id)

        created = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.action == AuditAction.RECORD_CREATED)
                .where(AuditLog.entity_type == "client")
            )
        ).scalar_one()
        assert created.entity_id == client.id
        assert created.metadata_["source_lead_id"] == str(lead.id)

    async def test_a_failed_conversion_leaves_no_audit_trail(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """A rolled-back action must not leave a record claiming it happened."""
        user, auth = admin
        service = ClientService(db, auth)
        lead = await LeadService(db, auth).create_lead(_lead_payload(), user)
        await service.convert_lead(lead.id, ClientConvert(), user)

        with pytest.raises(ConflictError):
            await service.convert_lead(lead.id, ClientConvert(), user)

        entries = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_CONVERTED)
            )
        ).scalars().all()
        assert len(entries) == 1, "The failed second conversion was audited."
