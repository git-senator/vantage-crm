"""Async database engine, session factory, and tenant context.

⚠ The single most dangerous bug class in this system lives in this file.

Row-level security reads the current tenant from a PostgreSQL runtime
parameter. That parameter MUST be set with `SET LOCAL`, which is scoped to the
enclosing transaction. A plain `SET` is scoped to the *connection*, and because
connections are pooled and reused across requests, it would leak one tenant's
scope into the next tenant's request — silently, and with no error anywhere.

See docs/DATABASE.md §2 and risk R8 in docs/ROADMAP.md.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_read_engine: AsyncEngine | None = None
_read_session_factory: async_sessionmaker[AsyncSession] | None = None

# The GUC that RLS policies read. Must match the policies in the migrations.
TENANT_GUC = "app.current_org"
ACTOR_GUC = "app.current_user"


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.DB_ECHO,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_pre_ping=True,  # survive DB restarts and idle disconnects
            pool_recycle=1800,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


def get_read_engine() -> AsyncEngine:
    """The engine for read-only work (Phase 8.1).

    A distinct pool against the read replica when one is configured, so reporting
    and analytics traffic does not consume the primary's connection budget. With
    no replica it *is* the primary engine — a single-node deployment pays nothing
    and the read path is always valid.
    """
    settings = get_settings()
    if not settings.has_read_replica:
        return get_engine()
    global _read_engine
    if _read_engine is None:
        _read_engine = create_async_engine(
            settings.read_database_url,
            echo=settings.DB_ECHO,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _read_engine


def get_read_session_factory() -> async_sessionmaker[AsyncSession]:
    # No replica: return the primary factory rather than a second one bound to
    # the same engine, so a rebinding (tests, a reload) is honoured on both paths.
    if not get_settings().has_read_replica:
        return get_session_factory()
    global _read_session_factory
    if _read_session_factory is None:
        _read_session_factory = async_sessionmaker(
            bind=get_read_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _read_session_factory


async def set_tenant_context(
    session: AsyncSession,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> None:
    """Bind the RLS tenant context to the CURRENT TRANSACTION.

    `SET LOCAL` is mandatory here — see the module docstring. Values are bound
    as parameters rather than interpolated, so this is not an injection point
    even though it is DDL-adjacent.
    """
    await session.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
        {"org": str(organization_id)},
    )
    if user_id is not None:
        await session.execute(
            text(f"SELECT set_config('{ACTOR_GUC}', :actor, true)"),
            {"actor": str(user_id)},
        )


@asynccontextmanager
async def session_scope(
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    """Transactional session scope, optionally tenant-bound.

    Used by workers and scripts. Request handling uses the FastAPI dependency
    in `app.api.v1.dependencies`, which wraps this.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            async with session.begin():
                if organization_id is not None:
                    await set_tenant_context(session, organization_id, user_id)
                yield session
        except Exception:
            # `session.begin()` rolls back on exception; this is for logging.
            logger.exception("db_transaction_rolled_back")
            raise


@asynccontextmanager
async def read_session_scope(
    organization_id: UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    """A tenant-bound, read-only session against the replica (Phase 8.1).

    Same RLS binding as `session_scope` — a read is not an excuse to run
    unscoped — but routed to the read endpoint. The transaction is opened READ
    ONLY, so a write is rejected by the database rather than silently landing on
    a replica that cannot accept it (or, with no replica, on the primary where it
    would be a correctness surprise). Falls back to the primary when no replica
    is configured.
    """
    factory = get_read_session_factory()
    async with factory() as session:
        try:
            async with session.begin():
                await session.execute(text("SET TRANSACTION READ ONLY"))
                if organization_id is not None:
                    await set_tenant_context(session, organization_id)
                yield session
        except Exception:
            logger.exception("db_read_transaction_rolled_back")
            raise


async def check_database() -> bool:
    """Readiness probe. Returns False rather than raising."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("database_healthcheck_failed")
        return False


async def check_read_database() -> bool:
    """Readiness probe for the read replica. True (trivially) when there is no
    replica — the read path is the primary, already covered by `check_database`."""
    settings = get_settings()
    if not settings.has_read_replica:
        return True
    try:
        async with get_read_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("read_database_healthcheck_failed")
        return False


async def dispose_engine() -> None:
    global _engine, _session_factory, _read_engine, _read_session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
    if _read_engine is not None:
        await _read_engine.dispose()
        _read_engine = None
        _read_session_factory = None
