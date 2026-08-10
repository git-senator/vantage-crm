"""Properties — shared inventory.

Mirrors tests/test_leads.py for the properties every CRM entity must have, but
the centrepiece is `TestSharedInventory`. Leads and clients are a personal book
of business; listings are not. An agent holds `properties.view` at ALL and
`properties.manage` at OWN, and that asymmetry is the whole reason scope is
modelled separately from permission.

The consequence worth pinning down: a listing the caller can see but not edit
returns **403, not 404**. Everywhere else in this codebase the answer is 404,
to avoid an existence oracle — but there is no secret to protect when the
record is already in the caller's own list.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.models.property import Property
from app.models.rbac import Team, TeamMember
from app.schemas.client import ClientCreate
from app.schemas.common import Cursor
from app.schemas.property import PropertyCreate, PropertyFilters, PropertyUpdate
from app.services.client import ClientService
from app.services.property import PropertyService
from app.services.property_translation import PropertyTranslationService
from app.services.rbac import AuthorizationContext
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


def _payload(**overrides: object) -> PropertyCreate:
    data: dict = {
        "title": "Restored Edwardian with garden",
        "address_line1": "1428 Sanchez Street",
        "city": "San Francisco",
        "state": "CA",
        "postal_code": "94131",
        "property_type": "single_family",
        "status": "active",
        "price": Decimal("1895000.00"),
        "bedrooms": 4,
        "bathrooms": Decimal("2.5"),
        "square_feet": 2840,
        **overrides,
    }
    return PropertyCreate(**data)


# --------------------------------------------------------------------- tests


class TestSharedInventory:
    """The property that makes properties different from every other entity."""

    async def test_agent_sees_every_listing_in_the_brokerage(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        """properties.view is ALL scope. Inventory is shared, not personal.

        The equivalent test for leads asserts the opposite — an agent sees only
        their own. That contrast is the point.
        """
        mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent

        await PropertyService(db, mine_auth).create_property(
            _payload(title="Mine"), mine_user
        )
        await PropertyService(db, theirs_auth).create_property(
            _payload(title="Theirs", address_line1="99 Other Street"), theirs_user
        )

        rows, _ = await PropertyService(db, mine_auth).list_properties(
            filters=PropertyFilters(), limit=50, cursor=None
        )
        assert {r.title for r in rows} == {"Mine", "Theirs"}

    async def test_agent_can_read_a_colleagues_listing(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        _mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent
        theirs = await PropertyService(db, theirs_auth).create_property(
            _payload(), theirs_user
        )

        fetched = await PropertyService(db, mine_auth).get_property(theirs.id)
        assert fetched.id == theirs.id

    async def test_agent_cannot_edit_a_colleagues_listing(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        """403, not 404 — they can see it, so pretending it is missing is a lie."""
        mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent
        theirs = await PropertyService(db, theirs_auth).create_property(
            _payload(), theirs_user
        )

        with pytest.raises(PermissionDeniedError):
            await PropertyService(db, mine_auth).update_property(
                theirs.id, PropertyUpdate(status="sold"), mine_user
            )

    async def test_agent_cannot_delete_a_colleagues_listing(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent
        theirs = await PropertyService(db, theirs_auth).create_property(
            _payload(), theirs_user
        )

        with pytest.raises(PermissionDeniedError):
            await PropertyService(db, mine_auth).delete_property(theirs.id, mine_user)

    async def test_agent_can_edit_their_own_listing(
        self, db: AsyncSession, agent
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = agent
        service = PropertyService(db, auth)
        listing = await service.create_property(_payload(), user)

        updated = await service.update_property(
            listing.id, PropertyUpdate(status="pending"), user
        )
        assert updated.status == "pending"

    async def test_a_nonexistent_listing_is_still_404(
        self, db: AsyncSession, agent
    ) -> None:  # type: ignore[no-untyped-def]
        """The 403 branch must not swallow genuine absence."""
        import uuid

        user, auth = agent
        with pytest.raises(NotFoundError):
            await PropertyService(db, auth).update_property(
                uuid.uuid4(), PropertyUpdate(status="sold"), user
            )

    async def test_cross_tenant_listing_is_404_not_403(
        self,
        db: AsyncSession,
        agent,
        other_organization: Organization,
        rbac_seeded: None,
    ) -> None:  # type: ignore[no-untyped-def]
        """The oracle protection still holds where it actually matters.

        Shared inventory is shared *within* a brokerage. Another tenant's
        listing must be indistinguishable from one that does not exist.
        """
        user, auth = agent
        outsider = await make_user(db, other_organization, "outsider@meridian.example")
        outsider_auth = AuthorizationContext(
            user_id=outsider.id,
            organization_id=other_organization.id,
            role_keys=("admin",),
            grants={"properties.view": Scope.ALL, "properties.manage": Scope.ALL},
        )
        foreign = await PropertyService(db, outsider_auth).create_property(
            _payload(), outsider
        )

        with pytest.raises(NotFoundError):
            await PropertyService(db, auth).update_property(
                foreign.id, PropertyUpdate(status="sold"), user
            )

    async def test_a_caller_without_view_gets_404_not_403(
        self, db: AsyncSession, agent, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """The 403 branch is conditional on actually holding properties.view.

        Otherwise a caller who cannot see listings at all would be told one
        exists — reintroducing exactly the oracle this design avoids.
        """
        user, auth = agent
        listing = await PropertyService(db, auth).create_property(_payload(), user)

        # manage-without-view is contrived, but a custom role can express it,
        # and it is precisely the case where the 403 branch must not fire.
        stranger = await make_user(db, organization, "stranger@vantage.example")
        blind = AuthorizationContext(
            user_id=stranger.id,
            organization_id=organization.id,
            role_keys=("odd",),
            grants={"properties.manage": Scope.OWN},
        )

        with pytest.raises(NotFoundError):
            await PropertyService(db, blind).update_property(
                listing.id, PropertyUpdate(status="sold"), stranger
            )


class TestCreate:
    async def test_creates_a_listing_owned_by_the_creator(
        self, db: AsyncSession, agent
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = agent
        listing = await PropertyService(db, auth).create_property(_payload(), user)

        assert listing.listing_agent_id == user.id
        assert listing.organization_id == user.organization_id
        assert listing.created_by == user.id
        assert listing.full_address.startswith("1428 Sanchez Street")

    async def test_requires_the_manage_permission(
        self, db: AsyncSession, organization: Organization, rbac_seeded: None
    ) -> None:
        user = await make_user(db, organization, "viewer@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("viewer",),
            grants={"properties.view": Scope.ALL},
        )
        with pytest.raises(PermissionDeniedError):
            await PropertyService(db, auth).create_property(_payload(), user)

    async def test_agent_cannot_assign_to_someone_else(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = agent
        target, _ = other_agent

        with pytest.raises(PermissionDeniedError):
            await PropertyService(db, auth).create_property(
                _payload(listing_agent_id=target.id), user
            )

    async def test_cannot_assign_to_a_user_from_another_tenant(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        outsider = await make_user(db, other_organization, "outsider@meridian.example")

        with pytest.raises(NotFoundError):
            await PropertyService(db, auth).create_property(
                _payload(listing_agent_id=outsider.id), user
            )

    async def test_links_a_seller_client(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        client = await ClientService(db, auth).create_client(
            ClientCreate(first_name="Nadia", last_name="Okonkwo", type="seller"), user
        )

        listing = await PropertyService(db, auth).create_property(
            _payload(client_id=client.id), user
        )
        assert listing.client_id == client.id

    async def test_rejects_a_client_from_another_tenant(
        self, db: AsyncSession, admin, other_organization: Organization
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
            await PropertyService(db, auth).create_property(
                _payload(client_id=foreign_client.id), user
            )

    async def test_price_may_be_omitted(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        """"Price on application" is a real listing state; 0 would corrupt averages."""
        user, auth = admin
        listing = await PropertyService(db, auth).create_property(
            _payload(price=None), user
        )
        assert listing.price is None

    async def test_negative_price_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            _payload(price=Decimal("-1"))

    async def test_half_bathrooms_survive(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        """The reason bathrooms is NUMERIC(3,1) and not an integer."""
        user, auth = admin
        listing = await PropertyService(db, auth).create_property(
            _payload(bathrooms=Decimal("2.5")), user
        )
        assert listing.bathrooms == Decimal("2.5")


class TestCoordinates:
    async def test_one_coordinate_alone_is_rejected(self) -> None:
        """A latitude without a longitude puts the listing on the meridian."""
        with pytest.raises(ValueError, match="together"):
            _payload(latitude=Decimal("37.75"))

    async def test_both_together_are_accepted(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        listing = await PropertyService(db, auth).create_property(
            _payload(latitude=Decimal("37.75"), longitude=Decimal("-122.43")), user
        )
        assert listing.latitude == Decimal("37.750000")

    async def test_out_of_range_latitude_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            _payload(latitude=Decimal("91"), longitude=Decimal("0"))

    async def test_patch_cannot_orphan_a_coordinate(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Only checkable against the merged record, so the service does it."""
        user, auth = admin
        service = PropertyService(db, auth)
        listing = await service.create_property(
            _payload(latitude=Decimal("37.75"), longitude=Decimal("-122.43")), user
        )

        with pytest.raises(ConflictError):
            await service.update_property(
                listing.id, PropertyUpdate(latitude=None), user
            )


class TestMlsNumber:
    async def test_duplicate_mls_within_a_tenant_is_a_conflict(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(_payload(mls_number="MLS-4471"), user)

        with pytest.raises(ConflictError):
            await service.create_property(
                _payload(mls_number="MLS-4471", address_line1="2 Other St"), user
            )

    async def test_the_same_mls_may_exist_in_another_tenant(
        self,
        db: AsyncSession,
        admin,
        other_organization: Organization,
        rbac_seeded: None,
    ) -> None:  # type: ignore[no-untyped-def]
        """The index is per-organization; MLS numbers are only unique per market."""
        user, auth = admin
        await PropertyService(db, auth).create_property(
            _payload(mls_number="MLS-4471"), user
        )

        outsider = await make_user(db, other_organization, "outsider@meridian.example")
        outsider_auth = AuthorizationContext(
            user_id=outsider.id,
            organization_id=other_organization.id,
            role_keys=("admin",),
            grants={"properties.manage": Scope.ALL, "properties.view": Scope.ALL},
        )
        listing = await PropertyService(db, outsider_auth).create_property(
            _payload(mls_number="MLS-4471"), outsider
        )
        assert listing.mls_number == "MLS-4471"

    async def test_many_listings_may_have_no_mls_number(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The index is partial for exactly this reason."""
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(_payload(title="A"), user)
        await service.create_property(_payload(title="B"), user)

        rows, _ = await service.list_properties(
            filters=PropertyFilters(), limit=50, cursor=None
        )
        assert len(rows) == 2


class TestDaysOnMarket:
    async def test_is_derived_from_listed_at(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        listing = await PropertyService(db, auth).create_property(
            _payload(listed_at=date.today() - timedelta(days=27)), user
        )
        assert listing.days_on_market == 27

    async def test_is_null_without_a_listing_date(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        listing = await PropertyService(db, auth).create_property(
            _payload(listed_at=None), user
        )
        assert listing.days_on_market is None

    async def test_freezes_once_sold(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        """A sold home does not keep accruing days on market."""
        user, auth = admin
        listing = await PropertyService(db, auth).create_property(
            _payload(listed_at=date.today() - timedelta(days=27), status="sold"), user
        )
        assert listing.days_on_market is None


class TestTenantIsolation:
    async def test_listings_never_cross_organizations(
        self,
        db: AsyncSession,
        admin,
        other_organization: Organization,
        rbac_seeded: None,
    ) -> None:  # type: ignore[no-untyped-def]
        _, admin_auth = admin
        outsider = await make_user(db, other_organization, "outsider@meridian.example")
        outsider_auth = AuthorizationContext(
            user_id=outsider.id,
            organization_id=other_organization.id,
            role_keys=("admin",),
            grants={"properties.view": Scope.ALL, "properties.manage": Scope.ALL},
        )

        await PropertyService(db, outsider_auth).create_property(
            _payload(title="Foreign"), outsider
        )

        rows, _ = await PropertyService(db, admin_auth).list_properties(
            filters=PropertyFilters(), limit=50, cursor=None
        )
        assert all(r.title != "Foreign" for r in rows)

    async def test_rls_blocks_an_unscoped_query(
        self, db: AsyncSession, admin, organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        """RLS must hold even when the repository predicate is bypassed."""
        user, auth = admin
        await PropertyService(db, auth).create_property(_payload(), user)
        await db.commit()

        from app.db.sql_objects import tenant_policy_statements

        for statement in tenant_policy_statements("properties"):
            await db.execute(text(statement))
        await db.commit()

        try:
            async with db.begin():
                rows = (await db.execute(select(Property))).unique().scalars().all()
            assert rows == [], (
                "An unscoped query returned rows with no tenant context bound. "
                "RLS is not enforcing on properties."
            )
        finally:
            await db.execute(
                text("DROP POLICY IF EXISTS tenant_isolation ON properties")
            )
            await db.execute(
                text("ALTER TABLE properties NO FORCE ROW LEVEL SECURITY")
            )
            await db.execute(text("ALTER TABLE properties DISABLE ROW LEVEL SECURITY"))
            await db.commit()


class TestPagination:
    async def test_pages_do_not_overlap_or_skip(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        for index in range(12):
            await service.create_property(_payload(title=f"Listing{index:02d}"), user)

        seen: list[str] = []
        cursor = None
        for _ in range(5):
            rows, has_more = await service.list_properties(
                filters=PropertyFilters(), limit=5, cursor=cursor
            )
            seen.extend(r.title for r in rows)
            if not has_more or not rows:
                break
            cursor = Cursor(created_at=rows[-1].created_at, id=rows[-1].id)

        assert len(seen) == 12
        assert len(set(seen)) == 12, "a row appeared on two pages"

    async def test_has_more_is_accurate(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        for index in range(3):
            await service.create_property(_payload(title=f"P{index}"), user)

        _, has_more = await service.list_properties(
            filters=PropertyFilters(), limit=2, cursor=None
        )
        assert has_more is True

        _, has_more = await service.list_properties(
            filters=PropertyFilters(), limit=10, cursor=None
        )
        assert has_more is False


class TestFiltersAndSearch:
    async def test_filter_by_status(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(_payload(title="Active"), user)
        await service.create_property(_payload(title="Sold", status="sold"), user)

        rows, _ = await service.list_properties(
            filters=PropertyFilters(status="sold"), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Sold"]

    async def test_filter_by_type(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(_payload(title="House"), user)
        await service.create_property(
            _payload(title="Flat", property_type="condo"), user
        )

        rows, _ = await service.list_properties(
            filters=PropertyFilters(property_type="condo"), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Flat"]

    async def test_price_range(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(
            _payload(title="Cheap", price=Decimal("500000")), user
        )
        await service.create_property(
            _payload(title="Mid", price=Decimal("1500000")), user
        )
        await service.create_property(
            _payload(title="Dear", price=Decimal("3000000")), user
        )

        rows, _ = await service.list_properties(
            filters=PropertyFilters(
                min_price=Decimal("1000000"), max_price=Decimal("2000000")
            ),
            limit=50,
            cursor=None,
        )
        assert [r.title for r in rows] == ["Mid"]

    async def test_incoherent_price_range_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_price cannot exceed max_price"):
            PropertyFilters(
                min_price=Decimal("2000000"), max_price=Decimal("1000000")
            )

    async def test_filter_by_minimum_bedrooms(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(_payload(title="Small", bedrooms=1), user)
        await service.create_property(_payload(title="Large", bedrooms=5), user)

        rows, _ = await service.list_properties(
            filters=PropertyFilters(min_bedrooms=3), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Large"]

    async def test_filter_by_city_is_case_insensitive(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(_payload(title="SF", city="San Francisco"), user)
        await service.create_property(_payload(title="Oak", city="Oakland"), user)

        rows, _ = await service.list_properties(
            filters=PropertyFilters(city="san francisco"), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["SF"]

    async def test_filter_by_feature(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(
            _payload(title="Pooled", features=["pool", "garage"]), user
        )
        await service.create_property(_payload(title="Plain"), user)

        rows, _ = await service.list_properties(
            filters=PropertyFilters(feature="pool"), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Pooled"]

    async def test_filter_by_client(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        client = await ClientService(db, auth).create_client(
            ClientCreate(first_name="Nadia", last_name="Okonkwo", type="seller"), user
        )
        service = PropertyService(db, auth)
        await service.create_property(
            _payload(title="Hers", client_id=client.id), user
        )
        await service.create_property(_payload(title="Unlinked"), user)

        rows, _ = await service.list_properties(
            filters=PropertyFilters(client_id=client.id), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Hers"]

    async def test_search_matches_an_address(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(
            _payload(title="A", address_line1="1428 Sanchez Street"), user
        )
        await service.create_property(
            _payload(title="B", address_line1="900 Divisadero Street"), user
        )
        await db.flush()

        rows, _ = await service.list_properties(
            filters=PropertyFilters(search="Sanchez"), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["A"]

    async def test_search_matches_an_mls_number(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Agents search by MLS id constantly; it belongs in the vector."""
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(
            _payload(title="Listed", mls_number="MLS-4471"), user
        )
        await service.create_property(_payload(title="Other"), user)
        await db.flush()

        rows, _ = await service.list_properties(
            filters=PropertyFilters(search="MLS-4471"), limit=50, cursor=None
        )
        assert [r.title for r in rows] == ["Listed"]

    async def test_search_accepts_punctuation(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(_payload(), user)
        await db.flush()

        for term in ["sanchez &", "!!!", "a | b", '"unclosed']:
            rows, _ = await service.list_properties(
                filters=PropertyFilters(search=term), limit=10, cursor=None
            )
            assert isinstance(rows, list)


class TestAssign:
    async def test_agent_cannot_assign_at_all(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = agent
        target, _ = other_agent
        listing = await PropertyService(db, auth).create_property(_payload(), user)

        with pytest.raises(PermissionDeniedError):
            await PropertyService(db, auth).assign_property(
                listing.id, target.id, user
            )

    async def test_admin_can_reassign(self, db: AsyncSession, admin, agent) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        target, _ = agent
        listing = await PropertyService(db, auth).create_property(_payload(), user)

        reassigned = await PropertyService(db, auth).assign_property(
            listing.id, target.id, user
        )
        assert reassigned.listing_agent_id == target.id

    async def test_manager_cannot_assign_outside_the_team(
        self, db: AsyncSession, organization: Organization, rbac_seeded: None
    ) -> None:
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
        service = PropertyService(db, manager_auth)
        listing = await service.create_property(_payload(), manager)

        with pytest.raises(PermissionDeniedError):
            await service.assign_property(listing.id, outsider.id, manager)


class TestUpdateAndDelete:
    async def test_partial_update_touches_only_given_fields(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        listing = await service.create_property(
            _payload(description="original copy"), user
        )

        updated = await service.update_property(
            listing.id, PropertyUpdate(status="pending"), user
        )

        assert updated.status == "pending"
        assert updated.description == "original copy"

    async def test_server_generated_columns_are_readable_after_update(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Every field the response model reads must be available without IO."""
        user, auth = admin
        service = PropertyService(db, auth)
        listing = await service.create_property(_payload(), user)

        updated = await service.update_property(
            listing.id, PropertyUpdate(status="pending"), user
        )

        assert updated.updated_at is not None
        assert updated.created_at is not None
        assert updated.full_address
        assert updated.status == "pending"

    async def test_soft_delete_hides_but_retains(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        listing = await service.create_property(_payload(), user)

        await service.delete_property(listing.id, user)

        with pytest.raises(NotFoundError):
            await service.get_property(listing.id)

        row = (
            await db.execute(select(Property).where(Property.id == listing.id))
        ).unique().scalar_one()
        assert row.deleted_at is not None

    async def test_deleting_twice_is_a_404(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        listing = await service.create_property(_payload(), user)
        await service.delete_property(listing.id, user)

        with pytest.raises(NotFoundError):
            await service.delete_property(listing.id, user)

    async def test_deleting_frees_the_mls_number(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The unique index excludes soft-deleted rows, so a property can be
        re-listed after its first listing is withdrawn."""
        user, auth = admin
        service = PropertyService(db, auth)
        first = await service.create_property(_payload(mls_number="MLS-4471"), user)
        await service.delete_property(first.id, user)

        relisted = await service.create_property(
            _payload(mls_number="MLS-4471"), user
        )
        assert relisted.mls_number == "MLS-4471"


class TestStatusCounts:
    async def test_counts_by_status(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(_payload(title="A"), user)
        await service.create_property(_payload(title="B"), user)
        await service.create_property(_payload(title="C", status="sold"), user)

        assert await service.status_counts() == {"active": 2, "sold": 1}

    async def test_counts_span_the_brokerage_for_an_agent(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        """Consistent with the list: shared inventory means shared counts."""
        mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent

        await PropertyService(db, mine_auth).create_property(_payload(), mine_user)
        await PropertyService(db, theirs_auth).create_property(
            _payload(address_line1="2 Other St"), theirs_user
        )

        assert await PropertyService(db, mine_auth).status_counts() == {"active": 2}


class TestAudit:
    async def test_create_is_audited(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        listing = await PropertyService(db, auth).create_property(_payload(), user)

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_CREATED)
            )
        ).scalar_one()
        assert entry.entity_type == "property"
        assert entry.entity_id == listing.id
        assert entry.actor_id == user.id

    async def test_update_records_a_field_diff(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        listing = await service.create_property(_payload(status="active"), user)

        await service.update_property(
            listing.id, PropertyUpdate(status="pending"), user
        )

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_UPDATED)
            )
        ).scalar_one()
        assert entry.metadata_["changes"]["status"] == {
            "old": "active",
            "new": "pending",
        }

    async def test_no_op_update_is_not_audited(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        listing = await service.create_property(_payload(status="active"), user)

        await service.update_property(
            listing.id, PropertyUpdate(status="active"), user
        )

        entries = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_UPDATED)
            )
        ).scalars().all()
        assert entries == []

    async def test_delete_is_audited(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        listing = await service.create_property(_payload(), user)
        await service.delete_property(listing.id, user)

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_DELETED)
            )
        ).scalar_one()
        assert entry.entity_id == listing.id

    async def test_a_rejected_edit_writes_nothing(
        self, db: AsyncSession, agent, other_agent
    ) -> None:  # type: ignore[no-untyped-def]
        """The 403 path must not leave an audit entry claiming a change."""
        mine_user, mine_auth = agent
        theirs_user, theirs_auth = other_agent
        theirs = await PropertyService(db, theirs_auth).create_property(
            _payload(), theirs_user
        )

        with pytest.raises(PermissionDeniedError):
            await PropertyService(db, mine_auth).update_property(
                theirs.id, PropertyUpdate(status="sold"), mine_user
            )

        entries = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_UPDATED)
            )
        ).scalars().all()
        assert entries == []


class TestRentAndSale:
    """A rent and a price are different quantities in the same column.

    Before `listing_kind` existed a rental's monthly figure sat in `price`
    beside a villa's asking price, so a filter for "under 100 000" answered
    with both and any average over the column was meaningless. These pin down
    that the two can no longer be confused for one another.
    """

    async def test_a_rental_gets_a_period_without_being_asked(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """"Per month" is what a rental means unless it says otherwise, so
        requiring the word would only make every caller type it."""
        user, auth = admin
        listing = await PropertyService(db, auth).create_property(
            _payload(listing_kind="rent", price=Decimal("4900.00")), user
        )
        assert listing.listing_kind == "rent"
        assert listing.rent_period == "month"

    async def test_a_sale_may_not_carry_a_period(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        """Not a shorthand for anything — it is two listings confused."""
        with pytest.raises(ValidationError):
            _payload(listing_kind="sale", rent_period="month")

    async def test_listings_default_to_sale(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        listing = await PropertyService(db, auth).create_property(_payload(), user)
        assert listing.listing_kind == "sale"
        assert listing.rent_period is None

    async def test_rentals_can_be_filtered_out_of_the_sale_book(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        await service.create_property(_payload(title="For sale"), user)
        await service.create_property(
            _payload(title="To rent", listing_kind="rent", price=Decimal("4900.00")),
            user,
        )

        rentals, _ = await service.list_properties(
            filters=PropertyFilters(listing_kind="rent"), limit=10, cursor=None
        )
        assert [row.title for row in rentals] == ["To rent"]


class TestOptionalPostcode:
    async def test_a_listing_may_have_no_postcode(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Plenty genuinely have none — a Brazilian development is known by its
        neighbourhood long before a CEP is issued. The column used to be NOT
        NULL, which meant such a listing had to be given a placeholder, and a
        placeholder prints on an export looking like a real postcode."""
        user, auth = admin
        listing = await PropertyService(db, auth).create_property(
            _payload(postal_code=None), user
        )
        assert listing.postal_code is None
        # And the composed address simply ends after the state, rather than
        # trailing whatever stood in for the missing value.
        assert listing.full_address.endswith("CA")


class TestTranslations:
    """Multilingual listing content.

    The rules that matter are about *not* losing work: a person's correction
    survives regeneration, and a listing is never served with an empty
    description because a translation has not been produced yet.
    """

    async def test_a_new_listing_records_the_language_it_was_written_in(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        listing = await PropertyService(db, auth).create_property(_payload(), user)
        assert listing.source_locale == "en"

    async def test_editing_a_translation_pins_it_against_the_machine(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """The point of the whole design: once a person has written it, a
        background job must not quietly replace it."""
        user, auth = admin
        service = PropertyService(db, auth)
        listing = await service.create_property(_payload(), user)

        row = await service.edit_translation(
            listing.id,
            "ru",
            title="Отреставрированный особняк",
            description="С садом.",
            features=[],
            actor=user,
        )
        assert row.is_machine is False
        assert row.edited_by_id == user.id
        assert row.edited_at is not None

        # `pending_locales` is what the job asks before spending a token.
        translations = PropertyTranslationService(db)
        pending = await translations.pending_locales(listing)
        assert "ru" not in pending
        assert "pt-BR" in pending

    async def test_a_source_edit_flags_human_text_instead_of_replacing_it(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        listing = await service.create_property(_payload(), user)
        await service.edit_translation(
            listing.id,
            "ru",
            title="Отреставрированный особняк",
            description="С садом.",
            features=[],
            actor=user,
        )

        await service.update_property(
            listing.id, PropertyUpdate(description="Now with a new roof."), user
        )
        listing = await service.get_property(listing.id)

        translations = PropertyTranslationService(db)
        flagged = await translations.mark_stale(listing)
        assert flagged == 1

        stored = await translations.for_property(listing.id)
        # The text is still the agent's, and it is now marked for review.
        assert stored["ru"].title == "Отреставрированный особняк"
        assert stored["ru"].is_stale is True
        # And it is still not something the machine may overwrite.
        assert "ru" not in await translations.pending_locales(listing)

    async def test_a_listing_cannot_translate_itself(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        """Writing the source language here would create a second, divergent
        copy of the listing's own text with no rule for which wins."""
        user, auth = admin
        service = PropertyService(db, auth)
        listing = await service.create_property(_payload(), user)

        with pytest.raises(ConflictError, match="written in en"):
            await service.edit_translation(
                listing.id,
                "en",
                title="Anything",
                description=None,
                features=[],
                actor=user,
            )

    async def test_unknown_locales_are_refused(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = PropertyService(db, auth)
        listing = await service.create_property(_payload(), user)

        with pytest.raises(ConflictError, match="Unknown locale"):
            await service.edit_translation(
                listing.id, "fr", title="x", description=None, features=[], actor=user
            )

    async def test_agents_cannot_translate_another_agents_listing(
        self, db: AsyncSession, admin, agent
    ) -> None:  # type: ignore[no-untyped-def]
        """Translations are not a side door around the listing's write rule."""
        owner_user, owner_auth = admin
        other_user, other_auth = agent
        listing = await PropertyService(db, owner_auth).create_property(
            _payload(), owner_user
        )

        with pytest.raises(PermissionDeniedError):
            await PropertyService(db, other_auth).edit_translation(
                listing.id,
                "ru",
                title="x",
                description=None,
                features=[],
                actor=other_user,
            )
