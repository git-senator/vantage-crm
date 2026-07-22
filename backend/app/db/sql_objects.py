"""SQL objects that ORM metadata cannot express.

Functions and row-level security policies have no SQLAlchemy model, so they
exist only as DDL. Defining them here — rather than inline in a migration —
means the migration and the test suite apply *the same* definitions.

Without this, tests build their schema from `Base.metadata` and silently lack
every policy and function the migration creates. A cross-tenant isolation test
would then pass while proving nothing, which is worse than having no test.

Consumers:
  - alembic/versions/c3d5e7f9a1b2_multitenancy_rls.py
  - tests/conftest.py
"""

from __future__ import annotations

# Tables keyed directly by organization_id.
TENANT_TABLES: tuple[str, ...] = ("users", "refresh_tokens")

# Role that owns the SECURITY DEFINER bootstrap functions. NOLOGIN + BYPASSRLS,
# and the owner of nothing else. See docker/postgres/init/01-roles.sql.
AUTH_OWNER_ROLE = "vantage_auth"

APP_ROLE = "vantage_app"


# Single place the tenant GUC is read, so every policy is identical.
# The `true` second argument makes a missing setting return NULL rather than
# raising — a NULL comparison matches no rows, so an unscoped session sees
# nothing. Failing closed is the point.
CURRENT_ORG_FUNCTION = """
CREATE OR REPLACE FUNCTION current_organization_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.current_org', true), '')::uuid
$$;
"""


# Login must find the account before the tenant is known, but RLS denies reads
# without tenant context. Returns ONLY the two ids — never the password hash,
# never profile data.
LOGIN_LOOKUP_FUNCTION = """
CREATE OR REPLACE FUNCTION lookup_login_identity(p_email citext)
RETURNS TABLE (user_id uuid, organization_id uuid)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
STABLE
AS $$
    SELECT u.id, u.organization_id
    FROM users u
    WHERE u.email = p_email
      AND u.deleted_at IS NULL
    ORDER BY u.created_at
    LIMIT 1
$$;
"""


# Background jobs have a third bootstrap problem, and it is the same shape.
#
# A scheduled sweep must act on every tenant, but `organizations` is RLS'd to
# `id = current_organization_id()` — so a worker with no context bound sees no
# organizations and the sweep silently does nothing. The tempting fix is to
# give the worker BYPASSRLS, which would exempt every query it makes from every
# policy: one broad grant to solve one narrow problem.
#
# Instead the worker gets exactly the list of ids, and then binds each one and
# does its actual work under RLS like any request. Nothing else crosses a
# tenant boundary. Returns ids only — never a name, never a setting.
ORGANIZATION_LIST_FUNCTION = """
CREATE OR REPLACE FUNCTION list_active_organization_ids()
RETURNS SETOF uuid
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
STABLE
AS $$
    SELECT o.id
    FROM organizations o
    WHERE o.deleted_at IS NULL
    ORDER BY o.created_at
$$;
"""


# Refresh and logout have the same bootstrap problem: an opaque token whose
# tenant must be resolved before RLS permits a read. Returns only the org id.
TOKEN_LOOKUP_FUNCTION = """
CREATE OR REPLACE FUNCTION lookup_token_organization(p_token_hash text)
RETURNS uuid
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
STABLE
AS $$
    SELECT rt.organization_id
    FROM refresh_tokens rt
    WHERE rt.token_hash = p_token_hash
    LIMIT 1
$$;
"""


def tenant_policy_statements(table: str, key: str = "organization_id") -> list[str]:
    """Enable, FORCE, and apply the isolation policy for one table.

    FORCE is not optional: a table's owner bypasses RLS by default, so without
    it every policy is silently inert while looking correct in the schema.
    """
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
        (
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({key} = current_organization_id()) "
            f"WITH CHECK ({key} = current_organization_id())"
        ),
    ]


def operational_policy_statements(table: str, key: str = "organization_id") -> list[str]:
    """Isolation for a table that also holds tenant-less infrastructure rows.

    `job_failures` is the case: a job that acts on one tenant's data records a
    failure against that tenant, but the scheduler's own sweep belongs to none.
    The standard policy would make those NULL rows invisible to everyone, which
    is precisely backwards — they are the failures an operator most needs to
    see, and they contain no customer data by construction.

    So: a NULL tenant is infrastructure and visible to anyone who can read the
    table at all; a non-NULL tenant obeys the usual rule.
    """
    predicate = f"({key} IS NULL OR {key} = current_organization_id())"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
        (
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING {predicate} WITH CHECK {predicate}"
        ),
    ]


def drop_tenant_policy_statements(table: str) -> list[str]:
    return [
        f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    ]


def all_policy_statements() -> list[str]:
    """Policies for every tenant-scoped table, in dependency order."""
    statements: list[str] = []
    for table in TENANT_TABLES:
        statements.extend(tenant_policy_statements(table))
    # organizations is keyed by its own id: a tenant sees exactly its own row.
    statements.extend(tenant_policy_statements("organizations", key="id"))
    return statements


def bootstrap_function_statements() -> list[str]:
    """Create the SECURITY DEFINER functions and lock down their grants."""
    return [
        CURRENT_ORG_FUNCTION,
        LOGIN_LOOKUP_FUNCTION,
        TOKEN_LOOKUP_FUNCTION,
        # PUBLIC would grant execute to every role, including future read-only ones.
        ORGANIZATION_LIST_FUNCTION,
        "REVOKE ALL ON FUNCTION lookup_login_identity(citext) FROM PUBLIC",
        "REVOKE ALL ON FUNCTION lookup_token_organization(text) FROM PUBLIC",
        "REVOKE ALL ON FUNCTION list_active_organization_ids() FROM PUBLIC",
    ]


def ownership_transfer_statement() -> str:
    """Hand the bootstrap functions to the BYPASSRLS role.

    Critical: `FORCE ROW LEVEL SECURITY` subjects even the table owner to
    policies. A SECURITY DEFINER function owned by the migration role returns
    zero rows, so EVERY LOGIN FAILS — silently, because the code path looks
    correct.

    Guarded by a role-existence check because CI and local test databases have
    no role separation; there the connecting superuser already bypasses RLS.
    """
    return f"""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{AUTH_OWNER_ROLE}') THEN
            ALTER FUNCTION lookup_login_identity(citext) OWNER TO {AUTH_OWNER_ROLE};
            ALTER FUNCTION lookup_token_organization(text) OWNER TO {AUTH_OWNER_ROLE};
            ALTER FUNCTION list_active_organization_ids() OWNER TO {AUTH_OWNER_ROLE};

            -- BYPASSRLS exempts a role from policies; it grants no table
            -- access. SELECT only — never INSERT, UPDATE or DELETE.
            GRANT SELECT ON users TO {AUTH_OWNER_ROLE};
            GRANT SELECT ON refresh_tokens TO {AUTH_OWNER_ROLE};
            GRANT SELECT ON organizations TO {AUTH_OWNER_ROLE};
        END IF;
    END
    $$;
    """


def app_grant_statements() -> list[str]:
    return [
        f"GRANT EXECUTE ON FUNCTION lookup_login_identity(citext) TO {APP_ROLE}",
        f"GRANT EXECUTE ON FUNCTION lookup_token_organization(text) TO {APP_ROLE}",
        f"GRANT EXECUTE ON FUNCTION current_organization_id() TO {APP_ROLE}",
        f"GRANT EXECUTE ON FUNCTION list_active_organization_ids() TO {APP_ROLE}",
    ]
