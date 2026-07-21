"""Cross-tenant isolation — the Phase 1 exit criterion.

Addresses risks R2 (multi-tenancy retrofit) and R8 (tenant context leaking
across pooled connections), both rated Critical in docs/ROADMAP.md.

These tests deliberately attempt to read another tenant's data with the
application's own query paths. A failure here is a cross-tenant data breach,
not a bug.

Two properties are proven separately:

1. **RLS alone is sufficient.** Repository-level scoping is disabled (queries
   are issued without an `organization_id` predicate) and the data must still
   be invisible. If this passes only because the repository filters, RLS is
   not actually doing anything and would not save us from a query written
   elsewhere.

2. **The context is transaction-scoped.** `SET LOCAL` must not survive into
   the next transaction on a reused connection.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.session import set_tenant_context
from app.db.sql_objects import all_policy_statements
from app.models.organization import Organization
from app.models.user import User

pytestmark = pytest.mark.integration


async def _rls_enabled(db: AsyncSession) -> bool:
    """True when the RLS migration has been applied to this database.

    The schema fixture builds tables from ORM metadata, which does not carry
    policies. Tests that need real policy enforcement apply them here.
    """
    result = await db.execute(
        text(
            "SELECT relrowsecurity AND relforcerowsecurity "
            "FROM pg_class WHERE relname = 'users'"
        )
    )
    return bool(result.scalar())


async def _apply_rls(db: AsyncSession) -> None:
    """Apply the same policies the migration creates.

    Statements come from `app.db.sql_objects`, the single source shared with
    migration c3d5e7f9a1b2 — so these tests can never drift out of step with
    what production actually enforces.
    """
    for statement in all_policy_statements():
        await db.execute(text(statement))
    await db.commit()


@pytest.fixture
async def two_tenants(
    db: AsyncSession, organization: Organization, other_organization: Organization
) -> tuple[Organization, Organization]:
    """Two organizations, each with one user, with RLS policies applied."""
    for org, email in (
        (organization, "insider@vantage.example"),
        (other_organization, "outsider@meridian.example"),
    ):
        db.add(
            User(
                organization_id=org.id,
                email=email,
                password_hash=hash_password("correct-horse-battery-staple"),
                full_name="Test Person",
            )
        )
    await db.commit()
    await _apply_rls(db)
    return organization, other_organization


class TestRowLevelSecurity:
    """RLS must isolate tenants without help from application-level filters."""

    async def test_policies_are_forced(
        self, db: AsyncSession, two_tenants: tuple[Organization, Organization]
    ) -> None:
        """FORCE is what stops the table owner bypassing every policy."""
        assert await _rls_enabled(db), (
            "RLS is not FORCEd on users. Without FORCE the owner bypasses "
            "every policy and tenant isolation is silently inert."
        )

    async def test_unscoped_query_sees_only_the_bound_tenant(
        self, db: AsyncSession, two_tenants: tuple[Organization, Organization]
    ) -> None:
        """The core proof: no organization_id predicate, yet only one tenant.

        This is the query an engineer writes when they forget to scope. RLS —
        not the repository — is what must save them.
        """
        insider, outsider = two_tenants

        async with db.begin():
            await set_tenant_context(db, insider.id)
            rows = (await db.execute(select(User))).scalars().all()

        assert len(rows) == 1
        assert rows[0].organization_id == insider.id
        assert all(row.organization_id != outsider.id for row in rows)

    async def test_direct_fetch_by_id_across_tenants_returns_nothing(
        self, db: AsyncSession, two_tenants: tuple[Organization, Organization]
    ) -> None:
        """Knowing another tenant's primary key must not be enough."""
        insider, outsider = two_tenants

        async with db.begin():
            await set_tenant_context(db, outsider.id)
            victim_id = (
                await db.execute(select(User.id).where(User.organization_id == outsider.id))
            ).scalar_one()

        async with db.begin():
            await set_tenant_context(db, insider.id)
            found = (
                await db.execute(select(User).where(User.id == victim_id))
            ).scalar_one_or_none()

        assert found is None, "RLS did not block a cross-tenant fetch by primary key"

    async def test_organizations_are_isolated(
        self, db: AsyncSession, two_tenants: tuple[Organization, Organization]
    ) -> None:
        insider, _outsider = two_tenants

        async with db.begin():
            await set_tenant_context(db, insider.id)
            visible = (await db.execute(select(Organization))).scalars().all()

        assert [org.id for org in visible] == [insider.id]

    async def test_no_tenant_context_sees_nothing(
        self, db: AsyncSession, two_tenants: tuple[Organization, Organization]
    ) -> None:
        """Fail closed: an unbound session must not see everything."""
        async with db.begin():
            rows = (await db.execute(select(User))).scalars().all()

        assert rows == [], (
            "A session with no tenant context returned rows. The policy must "
            "compare against a NULL GUC and therefore match nothing."
        )

    async def test_cannot_write_into_another_tenant(
        self, db: AsyncSession, two_tenants: tuple[Organization, Organization]
    ) -> None:
        """WITH CHECK blocks inserting a row attributed to another tenant."""
        insider, outsider = two_tenants

        with pytest.raises((ProgrammingError, Exception)):
            async with db.begin():
                await set_tenant_context(db, insider.id)
                db.add(
                    User(
                        organization_id=outsider.id,  # forged
                        email=f"forged-{uuid.uuid4().hex[:6]}@example.com",
                        password_hash=hash_password("x" * 16),
                        full_name="Forged Row",
                    )
                )
                await db.flush()


class TestTransactionScopedContext:
    """Guards risk R8 — the failure that would be silent and severe."""

    async def test_context_does_not_survive_the_transaction(
        self, db: AsyncSession, two_tenants: tuple[Organization, Organization]
    ) -> None:
        """`SET LOCAL` must not leak into the next transaction.

        Connections are pooled. If the tenant GUC persisted, the next request
        on that connection would inherit the previous tenant's scope — a
        cross-tenant leak with no error raised anywhere.
        """
        insider, _ = two_tenants

        async with db.begin():
            await set_tenant_context(db, insider.id)
            bound = (
                await db.execute(text("SELECT current_setting('app.current_org', true)"))
            ).scalar()
            assert bound == str(insider.id)

        # New transaction, same connection.
        async with db.begin():
            leaked = (
                await db.execute(text("SELECT current_setting('app.current_org', true)"))
            ).scalar()

        assert leaked in (None, ""), (
            f"Tenant context leaked across transactions (got {leaked!r}). "
            "set_config must be called with is_local=true."
        )

    async def test_switching_tenants_switches_visibility(
        self, db: AsyncSession, two_tenants: tuple[Organization, Organization]
    ) -> None:
        """Two consecutive transactions on one connection must not bleed."""
        insider, outsider = two_tenants

        async with db.begin():
            await set_tenant_context(db, insider.id)
            first = (await db.execute(select(User.organization_id))).scalars().all()

        async with db.begin():
            await set_tenant_context(db, outsider.id)
            second = (await db.execute(select(User.organization_id))).scalars().all()

        assert set(first) == {insider.id}
        assert set(second) == {outsider.id}


class TestLoginBootstrap:
    """The SECURITY DEFINER escape hatch must work, and stay narrow.

    Regression guard for a bug that would have broken login completely:
    `FORCE ROW LEVEL SECURITY` subjects even the table owner to policies, so a
    SECURITY DEFINER function owned by the migration role returns zero rows.
    Every login would have failed with "invalid credentials" while the code
    path looked entirely correct.

    The fix is a dedicated NOLOGIN + BYPASSRLS role that owns nothing but these
    functions. These tests mirror migration c3d5e7f9a1b2 exactly.
    """

    @staticmethod
    async def _create_lookup_function(db: AsyncSession) -> bool:
        """Create the function and hand it to the BYPASSRLS owner.

        Returns False when `vantage_auth` is absent (CI, where the connecting
        role is a superuser and no role separation exists).
        """
        await db.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION lookup_login_identity(p_email citext)
                RETURNS TABLE (user_id uuid, organization_id uuid)
                LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp
                STABLE AS $$
                    SELECT u.id, u.organization_id FROM users u
                    WHERE u.email = p_email AND u.deleted_at IS NULL
                    ORDER BY u.created_at LIMIT 1
                $$;
                """
            )
        )
        has_role = bool(
            (
                await db.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = 'vantage_auth'")
                )
            ).scalar()
        )
        if has_role:
            await db.execute(
                text("ALTER FUNCTION lookup_login_identity(citext) OWNER TO vantage_auth")
            )
            # BYPASSRLS skips policies but grants no table access on its own.
            await db.execute(text("GRANT SELECT ON users TO vantage_auth"))
        await db.commit()
        return has_role

    async def test_lookup_resolves_identity_despite_forced_rls(
        self, db: AsyncSession, two_tenants: tuple[Organization, Organization]
    ) -> None:
        """The bug that broke login: FORCE RLS starving the bootstrap function."""
        insider, _ = two_tenants
        owned_by_bypass_role = await self._create_lookup_function(db)

        if not owned_by_bypass_role:
            pytest.skip("vantage_auth role absent — role separation not provisioned")

        async with db.begin():
            row = (
                await db.execute(
                    text(
                        "SELECT user_id, organization_id FROM lookup_login_identity("
                        "CAST('insider@vantage.example' AS citext))"
                    )
                )
            ).first()

        assert row is not None, (
            "lookup_login_identity returned nothing under FORCE RLS. Its owner "
            "must be a BYPASSRLS role, or every login fails."
        )
        assert row[1] == insider.id

    async def test_lookup_returns_ids_only(
        self, db: AsyncSession, two_tenants: tuple[Organization, Organization]
    ) -> None:
        """It must never expose a password hash or profile data."""
        await self._create_lookup_function(db)

        async with db.begin():
            signature = (
                await db.execute(
                    text(
                        "SELECT pg_get_function_result(oid) FROM pg_proc "
                        "WHERE proname = 'lookup_login_identity'"
                    )
                )
            ).scalar_one()

        # Exactly two output columns, both ids. Any password or profile field
        # appearing here would widen the escape hatch.
        assert "user_id uuid" in signature
        assert "organization_id uuid" in signature
        assert "password" not in signature.lower()
        assert signature.count(",") == 1, (
            f"Bootstrap function exposes more than two columns: {signature}"
        )
