"""billing — plans, subscriptions, invoices

Phase 7.5. A global plan catalogue (no tenant, no RLS), plus two tenant-scoped,
RLS-FORCEd tables: one subscription per organization and its invoice history.
Seeds the default plans.

Revision ID: d7a3c1e9f0b2
Revises: c6f2a9b4e1d8
Created: 2026-08-04 09:00:00.000000

"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import (
    drop_tenant_policy_statements,
    tenant_policy_statements,
)
from app.services.billing.catalog import PLAN_DEFS

revision: str = 'd7a3c1e9f0b2'
down_revision: str | None = 'c6f2a9b4e1d8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'billing_plans',
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('price_cents', sa.Integer(), server_default='0', nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='USD', nullable=False),
        sa.Column('included_seats', sa.Integer(), server_default='1', nullable=False),
        sa.Column('price_per_seat_cents', sa.Integer(), server_default='0', nullable=False),
        sa.Column('features', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('quotas', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('provider_price_id', sa.String(length=120), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('is_public', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key', name='uq_billing_plans_key'),
    )

    op.create_table(
        'subscriptions',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('plan_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='active', nullable=False),
        sa.Column('seats', sa.Integer(), server_default='1', nullable=False),
        sa.Column('provider', sa.String(length=20), server_default='manual', nullable=False),
        sa.Column('provider_customer_id', sa.String(length=120), nullable=True),
        sa.Column('provider_subscription_id', sa.String(length=120), nullable=True),
        sa.Column('current_period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_at_period_end', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('trial_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('grace_period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['plan_id'], ['billing_plans.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', name='uq_subscriptions_org'),
    )
    op.create_index(
        'ix_subscriptions_provider_sub', 'subscriptions', ['provider_subscription_id']
    )

    op.create_table(
        'invoices',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('subscription_id', sa.UUID(), nullable=True),
        sa.Column('provider_invoice_id', sa.String(length=120), nullable=True),
        sa.Column('number', sa.String(length=60), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='open', nullable=False),
        sa.Column('amount_due_cents', sa.Integer(), server_default='0', nullable=False),
        sa.Column('amount_paid_cents', sa.Integer(), server_default='0', nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='USD', nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('hosted_invoice_url', sa.String(length=2048), nullable=True),
        sa.Column('pdf_url', sa.String(length=2048), nullable=True),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_invoices_org', 'invoices', ['organization_id', 'created_at'])

    # subscriptions and invoices are tenant data; billing_plans is a global
    # catalogue with no organization_id and therefore no policy.
    for table in ("subscriptions", "invoices"):
        for statement in tenant_policy_statements(table):
            op.execute(statement)

    # Seed the default catalogue.
    plans = sa.table(
        'billing_plans',
        sa.column('id', sa.UUID()),
        sa.column('key', sa.String()),
        sa.column('name', sa.String()),
        sa.column('description', sa.Text()),
        sa.column('price_cents', sa.Integer()),
        sa.column('currency', sa.String()),
        sa.column('included_seats', sa.Integer()),
        sa.column('price_per_seat_cents', sa.Integer()),
        sa.column('features', postgresql.JSONB()),
        sa.column('quotas', postgresql.JSONB()),
        sa.column('is_public', sa.Boolean()),
    )
    op.bulk_insert(
        plans,
        [{"id": uuid.uuid4(), **definition} for definition in PLAN_DEFS],
    )


def downgrade() -> None:
    for table in ("invoices", "subscriptions"):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_invoices_org', table_name='invoices')
    op.drop_table('invoices')
    op.drop_index('ix_subscriptions_provider_sub', table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_table('billing_plans')
