"""pipelines, deals, stage history and activities

Phase 2.6a. Five tables, because a deal is not meaningful without the pipeline
it moves through or the history it leaves behind.

`pipeline_stages` and `deal_stage_history` carry `organization_id` even though
both are reachable through a parent. Every tenant-scoped table needs the column
directly: the RLS policy is `organization_id = current_organization_id()`, and
a policy that had to join through a parent would be slower and would silently
stop applying the moment someone wrote a query that did not join.

`deal_stage_history` and `activities` get no `deleted_at`. History that can be
soft-deleted is not evidence, and a timeline with holes is worse than no
timeline.

Seeding note: the default pipeline is backfilled for every existing tenant.
That requires reading `organizations`, which is FORCE RLS — so the policy is
lifted for the duration of the seed and restored immediately. See the comment
at `_seed_default_pipelines`.

Revision ID: d1e5f7a9b3c4
Revises: c9d4e6f8a3b2
Created: 2026-07-21 17:30:00.000000

"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements
from app.models.pipeline import DEFAULT_PIPELINE_NAME, DEFAULT_STAGES

revision: str = 'd1e5f7a9b3c4'
down_revision: str | None = 'c9d4e6f8a3b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("pipelines", "pipeline_stages", "deals", "deal_stage_history", "activities")


def upgrade() -> None:
    # ------------------------------------------------------------ pipelines
    op.create_table('pipelines',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=True),
    sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('length(name) > 0', name='ck_pipelines_name'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'name', name='uq_pipelines_org_name')
    )
    op.create_index(op.f('ix_pipelines_deleted_at'), 'pipelines', ['deleted_at'], unique=False)
    op.create_index('ix_pipelines_org', 'pipelines', ['organization_id'], unique=False)
    # Exactly one default per tenant, enforced by the database rather than by
    # a service check two concurrent writes could both pass.
    op.create_index('uq_pipelines_one_default', 'pipelines', ['organization_id'], unique=True, postgresql_where=sa.text('is_default AND deleted_at IS NULL'))

    # ------------------------------------------------------- pipeline_stages
    op.create_table('pipeline_stages',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('pipeline_id', sa.UUID(), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('position', sa.SmallInteger(), nullable=False),
    sa.Column('default_probability', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('is_won', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_lost', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(key) > 0', name='ck_pipeline_stages_key'),
    sa.CheckConstraint('length(name) > 0', name='ck_pipeline_stages_name'),
    sa.CheckConstraint('default_probability >= 0 AND default_probability <= 100', name='ck_pipeline_stages_probability'),
    sa.CheckConstraint('NOT (is_won AND is_lost)', name='ck_pipeline_stages_outcome'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['pipeline_id'], ['pipelines.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('pipeline_id', 'key', name='uq_pipeline_stages_key')
    )
    op.create_index('ix_pipeline_stages_org', 'pipeline_stages', ['organization_id'], unique=False)
    op.create_index('ix_pipeline_stages_pipeline_position', 'pipeline_stages', ['pipeline_id', 'position'], unique=False)

    # ---------------------------------------------------------------- deals
    op.create_table('deals',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=True),
    sa.Column('client_id', sa.UUID(), nullable=False),
    sa.Column('property_id', sa.UUID(), nullable=True),
    sa.Column('pipeline_id', sa.UUID(), nullable=False),
    sa.Column('stage_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('value', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('commission_amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('commission_rate', sa.Numeric(precision=5, scale=4), nullable=True),
    sa.Column('probability', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('priority', sa.String(length=10), server_default='medium', nullable=False),
    sa.Column('expected_close_date', sa.Date(), nullable=True),
    sa.Column('actual_close_date', sa.Date(), nullable=True),
    sa.Column('lost_reason', sa.Text(), nullable=True),
    sa.Column('custom_fields', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(lost_reason, ''))", persisted=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('length(title) > 0', name='ck_deals_title'),
    sa.CheckConstraint('value IS NULL OR value >= 0', name='ck_deals_value'),
    sa.CheckConstraint('commission_amount IS NULL OR commission_amount >= 0', name='ck_deals_commission_amount'),
    sa.CheckConstraint('commission_rate IS NULL OR (commission_rate >= 0 AND commission_rate <= 1)', name='ck_deals_commission_rate'),
    sa.CheckConstraint('probability >= 0 AND probability <= 100', name='ck_deals_probability'),
    sa.CheckConstraint("priority IN ('low', 'medium', 'high', 'urgent')", name='ck_deals_priority'),
    # RESTRICT on client, pipeline and stage: deleting any of them out from
    # under a live transaction must fail loudly, not orphan it.
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['pipeline_id'], ['pipelines.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['stage_id'], ['pipeline_stages.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_deals_deleted_at'), 'deals', ['deleted_at'], unique=False)
    op.create_index('ix_deals_org_client', 'deals', ['organization_id', 'client_id'], unique=False)
    op.create_index('ix_deals_org_created_id', 'deals', ['organization_id', 'created_at', 'id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_deals_org_expected_close', 'deals', ['organization_id', 'expected_close_date'], unique=False)
    op.create_index('ix_deals_org_owner_created', 'deals', ['organization_id', 'owner_id', 'created_at'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_deals_org_pipeline_stage', 'deals', ['organization_id', 'pipeline_id', 'stage_id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_deals_org_property', 'deals', ['organization_id', 'property_id'], unique=False)
    op.create_index('ix_deals_search', 'deals', ['search_vector'], unique=False, postgresql_using='gin')
    op.create_index('ix_deals_title_trgm', 'deals', [sa.literal_column("title gin_trgm_ops")], unique=False, postgresql_using='gin')

    # --------------------------------------------------- deal_stage_history
    op.create_table('deal_stage_history',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('deal_id', sa.UUID(), nullable=False),
    sa.Column('from_stage_id', sa.UUID(), nullable=True),
    sa.Column('to_stage_id', sa.UUID(), nullable=False),
    sa.Column('changed_by', sa.UUID(), nullable=True),
    sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('duration_in_stage', sa.Interval(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint('from_stage_id IS NULL OR from_stage_id <> to_stage_id', name='ck_deal_stage_history_moved'),
    sa.ForeignKeyConstraint(['changed_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['deal_id'], ['deals.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['from_stage_id'], ['pipeline_stages.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['to_stage_id'], ['pipeline_stages.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_deal_stage_history_deal', 'deal_stage_history', ['deal_id', 'changed_at'], unique=False)
    op.create_index('ix_deal_stage_history_from_stage', 'deal_stage_history', ['organization_id', 'from_stage_id'], unique=False)
    op.create_index('ix_deal_stage_history_org', 'deal_stage_history', ['organization_id', 'changed_at'], unique=False)

    # ----------------------------------------------------------- activities
    op.create_table('activities',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('entity_type', sa.String(length=20), nullable=False),
    sa.Column('entity_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("type IN ('call', 'email', 'meeting', 'note', 'showing', 'stage_change')", name='ck_activities_type'),
    sa.CheckConstraint("entity_type IN ('lead', 'client', 'property', 'deal')", name='ck_activities_entity_type'),
    sa.CheckConstraint('length(subject) > 0', name='ck_activities_subject'),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_activities_entity', 'activities', ['organization_id', 'entity_type', 'entity_id', sa.literal_column('occurred_at DESC')], unique=False)
    op.create_index('ix_activities_org_occurred', 'activities', ['organization_id', sa.literal_column('occurred_at DESC')], unique=False)

    # Seed BEFORE the policies exist, so the inserts are not subject to a
    # WITH CHECK that needs a tenant context this migration does not have.
    _seed_default_pipelines()

    for table in TENANT_TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def _seed_default_pipelines() -> None:
    """Give every existing tenant the default pipeline.

    Without this, every workspace that predates 2.6 can view the Deals page but
    cannot create a deal, because there is nowhere to put one.

    `organizations` is FORCE ROW LEVEL SECURITY, which subjects even the table
    owner to the policy — and this migration has no `app.current_org` bound, so
    a plain SELECT returns zero rows and the seed would silently do nothing.
    The policy is therefore lifted for the duration and restored immediately.
    Alembic runs migrations in a transaction, so a failure rolls the ALTER back
    with everything else.
    """
    connection = op.get_bind()

    op.execute("ALTER TABLE organizations NO FORCE ROW LEVEL SECURITY")
    try:
        org_ids = [
            row[0]
            for row in connection.execute(
                sa.text("SELECT id FROM organizations WHERE deleted_at IS NULL")
            )
        ]

        for org_id in org_ids:
            pipeline_id = uuid.uuid4()
            connection.execute(
                sa.text(
                    """
                    INSERT INTO pipelines (
                        id, organization_id, name, description, is_default,
                        created_at, updated_at
                    )
                    VALUES (
                        :id, :org, :name,
                        'The default sales process for this workspace.',
                        true, now(), now()
                    )
                    ON CONFLICT (organization_id, name) DO NOTHING
                    """
                ),
                {"id": pipeline_id, "org": org_id, "name": DEFAULT_PIPELINE_NAME},
            )

            for position, spec in enumerate(DEFAULT_STAGES):
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO pipeline_stages (
                            id, organization_id, pipeline_id, key, name, position,
                            default_probability, is_won, is_lost,
                            created_at, updated_at
                        )
                        VALUES (
                            gen_random_uuid(), :org, :pipeline, :key, :name,
                            :position, :probability, :is_won, :is_lost,
                            now(), now()
                        )
                        ON CONFLICT (pipeline_id, key) DO NOTHING
                        """
                    ),
                    {
                        "org": org_id,
                        "pipeline": pipeline_id,
                        "key": spec["key"],
                        "name": spec["name"],
                        "position": position,
                        "probability": spec["probability"],
                        "is_won": bool(spec.get("is_won", False)),
                        "is_lost": bool(spec.get("is_lost", False)),
                    },
                )
    finally:
        op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_activities_org_occurred', table_name='activities')
    op.drop_index('ix_activities_entity', table_name='activities')
    op.drop_table('activities')

    op.drop_index('ix_deal_stage_history_org', table_name='deal_stage_history')
    op.drop_index('ix_deal_stage_history_from_stage', table_name='deal_stage_history')
    op.drop_index('ix_deal_stage_history_deal', table_name='deal_stage_history')
    op.drop_table('deal_stage_history')

    op.drop_index('ix_deals_title_trgm', table_name='deals', postgresql_using='gin')
    op.drop_index('ix_deals_search', table_name='deals', postgresql_using='gin')
    op.drop_index('ix_deals_org_property', table_name='deals')
    op.drop_index('ix_deals_org_pipeline_stage', table_name='deals', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_deals_org_owner_created', table_name='deals', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_deals_org_expected_close', table_name='deals')
    op.drop_index('ix_deals_org_created_id', table_name='deals', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_deals_org_client', table_name='deals')
    op.drop_index(op.f('ix_deals_deleted_at'), table_name='deals')
    op.drop_table('deals')

    op.drop_index('ix_pipeline_stages_pipeline_position', table_name='pipeline_stages')
    op.drop_index('ix_pipeline_stages_org', table_name='pipeline_stages')
    op.drop_table('pipeline_stages')

    op.drop_index('uq_pipelines_one_default', table_name='pipelines', postgresql_where=sa.text('is_default AND deleted_at IS NULL'))
    op.drop_index('ix_pipelines_org', table_name='pipelines')
    op.drop_index(op.f('ix_pipelines_deleted_at'), table_name='pipelines')
    op.drop_table('pipelines')
