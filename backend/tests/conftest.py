"""Shared test fixtures.

Integration tests run against **real PostgreSQL**, never SQLite. SQLite does not
implement row-level security, so a suite running on it would report green while
tenant isolation did nothing whatsoever. That is not a hypothetical risk — RLS
is the primary tenant boundary in this system (docs/DATABASE.md §2).

Tests are skipped rather than failed when no database is reachable, so the pure
unit suite still runs on a laptop without Docker.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.sql_objects import (
    bootstrap_function_statements,
    drop_tenant_policy_statements,
    ownership_transfer_statement,
)
from app.models.organization import Organization
from app.models.user import User

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _database_url() -> str:
    # 127.0.0.1, not "localhost". On Windows and some Linux setups "localhost"
    # resolves to ::1 first; when Postgres only listens on IPv4 the connection
    # stalls on the IPv6 attempt before falling back. Measured at 2.08s vs
    # 0.05s per connection — a 40x difference that dominated the suite runtime.
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_TEST_DB", "vantage_test")
    # Tests run as the migration role: they create and drop the schema.
    user = os.getenv("POSTGRES_MIGRATION_USER", "vantage_migrator")
    password = os.getenv("POSTGRES_MIGRATION_PASSWORD", "dev_migrator_password")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"


@pytest.fixture
def settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        JWT_SECRET=TEST_JWT_SECRET,
        ENVIRONMENT="test",
        COOKIE_SECURE=False,
        LOGIN_MAX_ATTEMPTS=3,
        LOGIN_LOCKOUT_SECONDS=60,
    )


@pytest_asyncio.fixture
async def engine():  # type: ignore[no-untyped-def]
    """Per-test engine.

    Deliberately function-scoped with NullPool. asyncpg binds a connection to
    the event loop that created it, and pytest-asyncio gives each test its own
    loop — a session-scoped engine therefore fails with "attached to a different
    loop" as soon as a second test runs. Pooling across tests is not worth that
    class of flake.

    Skips (rather than fails) when no database is reachable, so the pure unit
    suite still runs without Docker.
    """
    engine = create_async_engine(_database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"PostgreSQL not reachable for integration tests: {exc}")
    yield engine
    await engine.dispose()


_SCHEMA_READY = False


@pytest_asyncio.fixture
async def db(engine) -> AsyncIterator[AsyncSession]:  # type: ignore[no-untyped-def]
    """A clean database per test, against real PostgreSQL.

    The schema is built once per process and then TRUNCATEd between tests.
    Dropping and recreating every table per test was measured at ~6.5s of DDL
    per test (3 minutes for this file alone); TRUNCATE gives the same isolation
    for a fraction of the cost.

    Isolation is still real: TRUNCATE ... CASCADE empties every table, and the
    schema — constraints, indexes, and later the RLS policies — is exactly what
    the migrations produce.
    """
    global _SCHEMA_READY

    async with engine.begin() as conn:
        if not _SCHEMA_READY:
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "citext"'))
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

            # ORM metadata carries no functions. These come from the same
            # module the migration uses, so the two cannot drift.
            for statement in bootstrap_function_statements():
                await conn.execute(text(statement))
            await conn.execute(text(ownership_transfer_statement()))

            _SCHEMA_READY = True
        else:
            tables = ", ".join(
                f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables)
            )
            if tables:
                await conn.execute(
                    text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
                )

        # Reset row-level security to off between tests.
        #
        # TRUNCATE clears rows but not policies, and policies are session-
        # independent DDL. Without this, the first test that enables RLS leaves
        # it on for every subsequent test, and ordinary fixtures can no longer
        # insert (no tenant context is bound). Tests that need RLS opt in
        # explicitly — see tests/test_tenant_isolation.py.
        for table in Base.metadata.sorted_tables:
            for statement in drop_tenant_policy_statements(table.name):
                await conn.execute(text(statement))

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def organization(db: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Vantage Realty Group",
        slug=f"vantage-{uuid.uuid4().hex[:8]}",
    )
    db.add(org)
    await db.flush()
    return org


@pytest_asyncio.fixture
async def other_organization(db: AsyncSession) -> Organization:
    """A second tenant. Exists so cross-tenant leakage can be proven, not assumed."""
    org = Organization(
        id=uuid.uuid4(),
        name="Meridian Properties",
        slug=f"meridian-{uuid.uuid4().hex[:8]}",
    )
    db.add(org)
    await db.flush()
    return org


VALID_PASSWORD = "correct-horse-battery-staple"


@pytest_asyncio.fixture
async def user(db: AsyncSession, organization: Organization) -> User:
    account = User(
        organization_id=organization.id,
        email="avery.chen@vantagerealty.example",
        password_hash=hash_password(VALID_PASSWORD),
        full_name="Avery Chen",
        job_title="Managing Broker",
        avatar_hue=268,
        status="active",
    )
    db.add(account)
    await db.flush()
    await db.refresh(account, ["organization"])
    return account
