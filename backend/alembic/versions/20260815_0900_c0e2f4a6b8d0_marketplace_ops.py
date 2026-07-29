"""marketplace operations — versions, installations, reviews

Phase 9.3. Three new tables layered on the 9.2 integration marketplace:

  * ``integration_versions`` — published versions of a listing. Shares the
    listing's *operational* RLS (NULL publisher = curated/global, else the
    tenant's own).
  * ``integration_installations`` — a tenant's operational install-history record.
    Tenant-scoped, RLS-FORCEd.
  * ``integration_reviews`` — review decisions on a tenant-authored listing.
    Tenant-scoped, RLS-FORCEd.

Also retunes ``integration_listings.status`` default from ``listed`` to ``draft``
to match the publication lifecycle (draft -> review -> approved -> published ->
deprecated -> retired). Curated listings are synced straight to ``published`` by
the application, so no data backfill is needed.

Revision ID: c0e2f4a6b8d0
Revises: b9d1f3a5c7e9
Created: 2026-08-15 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import (
    drop_tenant_policy_statements,
    operational_policy_statements,
    tenant_policy_statements,
)

revision: str = 'c0e2f4a6b8d0'
down_revision: str | None = 'b9d1f3a5c7e9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = ("integration_installations", "integration_reviews")


def upgrade() -> None:
    op.alter_column(
        "integration_listings",
        "status",
        server_default="draft",
        existing_type=sa.String(length=16),
        existing_nullable=False,
    )

    op.create_table(
        'integration_versions',
        sa.Column('publisher_organization_id', sa.UUID(), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('listing_id', sa.UUID(), nullable=False),
        sa.Column('version', sa.String(length=20), nullable=False),
        sa.Column('manifest', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('compatibility', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('status', sa.String(length=16), server_default='published', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['publisher_organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['listing_id'], ['integration_listings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('listing_id', 'version', name='uq_integration_versions'),
    )
    op.create_index(
        'ix_integration_versions_publisher', 'integration_versions',
        ['publisher_organization_id'],
    )
    op.create_index(
        'ix_integration_versions_listing', 'integration_versions', ['listing_id'],
    )

    op.create_table(
        'integration_installations',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('installed_by', sa.UUID(), nullable=True),
        sa.Column('listing_id', sa.UUID(), nullable=False),
        sa.Column('installed_plugin_id', sa.UUID(), nullable=True),
        sa.Column('installed_version', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
        sa.Column('installed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('uninstalled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['installed_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['listing_id'], ['integration_listings.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['installed_plugin_id'], ['plugins.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_integration_installations_org_status', 'integration_installations',
        ['organization_id', 'status'],
    )
    op.create_index(
        'ix_integration_installations_org_listing', 'integration_installations',
        ['organization_id', 'listing_id'],
    )

    op.create_table(
        'integration_reviews',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('listing_id', sa.UUID(), nullable=False),
        sa.Column('reviewer_id', sa.UUID(), nullable=True),
        sa.Column('decision', sa.String(length=16), nullable=False),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['listing_id'], ['integration_listings.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_integration_reviews_org_listing', 'integration_reviews',
        ['organization_id', 'listing_id'],
    )

    # integration_versions shares the listing's operational policy.
    for statement in operational_policy_statements(
        "integration_versions", key="publisher_organization_id"
    ):
        op.execute(statement)
    for table in _TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)
    for statement in drop_tenant_policy_statements("integration_versions"):
        op.execute(statement)

    op.drop_index(
        'ix_integration_reviews_org_listing', table_name='integration_reviews'
    )
    op.drop_table('integration_reviews')
    op.drop_index(
        'ix_integration_installations_org_listing',
        table_name='integration_installations',
    )
    op.drop_index(
        'ix_integration_installations_org_status',
        table_name='integration_installations',
    )
    op.drop_table('integration_installations')
    op.drop_index(
        'ix_integration_versions_listing', table_name='integration_versions'
    )
    op.drop_index(
        'ix_integration_versions_publisher', table_name='integration_versions'
    )
    op.drop_table('integration_versions')

    op.alter_column(
        "integration_listings",
        "status",
        server_default="listed",
        existing_type=sa.String(length=16),
        existing_nullable=False,
    )
