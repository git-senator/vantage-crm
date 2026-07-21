"""Alembic environment.

Runs under POSTGRES_MIGRATION_USER, not the application role. This matters:
PostgreSQL table owners bypass row-level security unless the table is declared
`FORCE ROW LEVEL SECURITY`. If migrations ran as the application role, that role
would own every table and tenant isolation would be inert while appearing
correct. See docs/DATABASE.md §2.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing the models package populates `Base.metadata`. A model that is not
# imported here is absent from the metadata, and autogenerate would emit a
# migration that DROPS its table.
import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.migration_database_url)


# Tables Alembic must not try to manage — extension-owned, not ours.
UNMANAGED_TABLES = frozenset({"spatial_ref_sys"})


def _include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """Keep autogenerate away from objects Alembic does not manage."""
    return not (type_ == "table" and name in UNMANAGED_TABLES)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a database connection.

    Used to review a migration before it touches production.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=_include_object,
            # One transaction per migration: a failure rolls back cleanly
            # instead of leaving the schema half-applied.
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
