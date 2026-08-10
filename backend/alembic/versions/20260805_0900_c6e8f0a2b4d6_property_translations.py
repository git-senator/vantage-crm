"""property translations — multilingual listing content

The UI ships in three languages and the toggle changes every label and none of
the content. This adds the content half.

``property_translations`` holds one row per (listing, language). It is a table
rather than a JSONB column on ``properties`` for one concrete reason: every
property mutation writes an audit entry in the same transaction, so a
background translator writing into the listing row would forge an audit entry
per translation and move each listing's last-modified. The audit log exists to
answer *who changed this*; it would start lying.

``properties.source_locale`` records the language the listing was actually
written in. Existing rows are assumed English — the app's default — except
those imported from the Rossa catalogue, which are Russian and are marked as
such here rather than left to be discovered later as forty-one listings
"translated" from English into English.

Revision ID: c6e8f0a2b4d6
Revises: b5d7e9f1a3c5
Created: 2026-08-05 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import (
    drop_tenant_policy_statements,
    tenant_policy_statements,
)

revision: str = "c6e8f0a2b4d6"
down_revision: str | None = "b5d7e9f1a3c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "properties",
        sa.Column(
            "source_locale",
            sa.String(length=10),
            nullable=False,
            server_default="en",
        ),
    )
    op.create_check_constraint(
        "ck_properties_source_locale",
        "properties",
        "source_locale IN ('en', 'pt-BR', 'ru')",
    )

    # The Rossa catalogue import stamps `custom_fields.source_slug`, which is
    # the only marker distinguishing those listings from anything an agent
    # typed. They are Russian.
    #
    # `properties` is FORCE ROW LEVEL SECURITY, which subjects even the table
    # owner to the policy, and a migration has no `app.current_org` bound — so
    # this UPDATE would match zero rows and report success. The policy is
    # lifted for the statement and restored immediately; Alembic runs the
    # migration in a transaction, so a failure rolls the ALTER back with
    # everything else. Same treatment as `_seed_default_pipelines` in
    # d1e5f7a9b3c4.
    op.execute("ALTER TABLE properties NO FORCE ROW LEVEL SECURITY")
    try:
        op.execute(
            """
            UPDATE properties
               SET source_locale = 'ru'
             WHERE custom_fields ? 'source_slug'
            """
        )
    finally:
        op.execute("ALTER TABLE properties FORCE ROW LEVEL SECURITY")

    op.create_table(
        "property_translations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locale", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "features",
            postgresql.ARRAY(sa.String(length=60)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "is_machine", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "is_stale", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("translated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("edited_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["property_id"], ["properties.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["edited_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("property_id", "locale", name="uq_property_translation"),
        sa.CheckConstraint(
            "locale IN ('en', 'pt-BR', 'ru')",
            name="ck_property_translations_locale",
        ),
        sa.CheckConstraint(
            "(edited_by_id IS NULL) = (edited_at IS NULL)",
            name="ck_property_translations_edit_provenance",
        ),
    )
    op.create_index(
        "ix_property_translations_stale",
        "property_translations",
        ["organization_id", "is_stale"],
    )
    op.create_index(
        "ix_property_translations_property",
        "property_translations",
        ["property_id"],
    )

    for statement in tenant_policy_statements("property_translations"):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_tenant_policy_statements("property_translations"):
        op.execute(statement)

    op.drop_index(
        "ix_property_translations_property", table_name="property_translations"
    )
    op.drop_index(
        "ix_property_translations_stale", table_name="property_translations"
    )
    op.drop_table("property_translations")

    op.drop_constraint("ck_properties_source_locale", "properties", type_="check")
    op.drop_column("properties", "source_locale")
