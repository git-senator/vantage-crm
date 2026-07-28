"""integration marketplace — curated integration listings

Phase 9.2. One table, ``integration_listings``, the marketplace registry. It uses
the *operational* RLS policy on ``publisher_organization_id`` — the same pattern
``plugins`` uses — so a NULL-publisher row is a curated listing visible to every
tenant and a non-NULL row is that tenant's private listing. The marketplace
reuses the plugin platform for installation, the Phase 9.1 diagnostics for health,
and the Phase 7.7 runtime for live connections; none of those tables are restated
here.

Revision ID: b9d1f3a5c7e9
Revises: a8c0e2f4b6d8
Created: 2026-08-14 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import (
    drop_tenant_policy_statements,
    operational_policy_statements,
)

revision: str = 'b9d1f3a5c7e9'
down_revision: str | None = 'a8c0e2f4b6d8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'integration_listings',
        sa.Column('publisher_organization_id', sa.UUID(), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('vendor', sa.String(length=120), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('summary', sa.String(length=300), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('auth_method', sa.String(length=16), nullable=False),
        sa.Column('oauth_scopes', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('provider_key', sa.String(length=60), nullable=True),
        sa.Column('certification', sa.String(length=16), server_default='community', nullable=False),
        sa.Column('manifest', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('capabilities', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('event_types', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('required_feature', sa.String(length=100), nullable=True),
        sa.Column('docs_url', sa.String(length=500), nullable=True),
        sa.Column('is_first_party', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('status', sa.String(length=16), server_default='listed', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['publisher_organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key', name='uq_integration_listings_key'),
    )
    op.create_index(
        'ix_integration_listings_publisher', 'integration_listings',
        ['publisher_organization_id'],
    )
    op.create_index(
        'ix_integration_listings_category', 'integration_listings', ['category'],
    )
    op.create_index(
        'ix_integration_listings_certification', 'integration_listings',
        ['certification'],
    )

    # The registry uses the operational policy: NULL publisher = curated (visible
    # to all), non-NULL = private to that tenant.
    for statement in operational_policy_statements(
        "integration_listings", key="publisher_organization_id"
    ):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_tenant_policy_statements("integration_listings"):
        op.execute(statement)
    op.drop_index(
        'ix_integration_listings_certification', table_name='integration_listings'
    )
    op.drop_index(
        'ix_integration_listings_category', table_name='integration_listings'
    )
    op.drop_index(
        'ix_integration_listings_publisher', table_name='integration_listings'
    )
    op.drop_table('integration_listings')
