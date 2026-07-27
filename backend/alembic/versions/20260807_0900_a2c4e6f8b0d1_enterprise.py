"""enterprise governance — security, branding, compliance, sso, features

Phase 8.0. Six tenant-scoped, RLS-FORCEd tables: security_policies,
organization_branding, compliance_policies, data_requests, sso_connections and
feature_flags. The four singleton policy tables carry UNIQUE(organization_id).
SSO client secret and certificate columns hold SecretBox ciphertext.

Revision ID: a2c4e6f8b0d1
Revises: f1a7c3d9e2b4
Created: 2026-08-07 09:00:00.000000

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

revision: str = 'a2c4e6f8b0d1'
down_revision: str | None = 'f1a7c3d9e2b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "security_policies",
    "organization_branding",
    "compliance_policies",
    "data_requests",
    "sso_connections",
    "feature_flags",
)


def upgrade() -> None:
    op.create_table(
        'security_policies',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('password_min_length', sa.Integer(), server_default='12', nullable=False),
        sa.Column('password_require_upper', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('password_require_lower', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('password_require_number', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('password_require_symbol', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('password_expiry_days', sa.Integer(), nullable=True),
        sa.Column('mfa_required', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('mfa_grace_days', sa.Integer(), server_default='7', nullable=False),
        sa.Column('session_idle_timeout_minutes', sa.Integer(), nullable=True),
        sa.Column('session_absolute_hours', sa.Integer(), nullable=True),
        sa.Column('max_concurrent_sessions', sa.Integer(), nullable=True),
        sa.Column('sessions_valid_after', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip_allowlist', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('ip_enforcement', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', name='uq_security_policies_org'),
    )

    op.create_table(
        'organization_branding',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('logo_url', sa.String(length=2048), nullable=True),
        sa.Column('icon_url', sa.String(length=2048), nullable=True),
        sa.Column('primary_color', sa.String(length=9), nullable=True),
        sa.Column('accent_color', sa.String(length=9), nullable=True),
        sa.Column('login_heading', sa.String(length=200), nullable=True),
        sa.Column('login_subheading', sa.String(length=500), nullable=True),
        sa.Column('email_from_name', sa.String(length=120), nullable=True),
        sa.Column('email_footer', sa.String(length=1000), nullable=True),
        sa.Column('support_url', sa.String(length=2048), nullable=True),
        sa.Column('custom_domain', sa.String(length=255), nullable=True),
        sa.Column('is_published', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', name='uq_organization_branding_org'),
    )

    op.create_table(
        'compliance_policies',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('retention_days', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('legal_hold', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('legal_hold_reason', sa.String(length=500), nullable=True),
        sa.Column('dpo_email', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', name='uq_compliance_policies_org'),
    )

    op.create_table(
        'data_requests',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('subject_email', sa.String(length=255), nullable=False),
        sa.Column('subject_user_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('result', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("kind IN ('export', 'deletion')", name='ck_data_requests_kind'),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name='ck_data_requests_status',
        ),
    )
    op.create_index('ix_data_requests_org', 'data_requests', ['organization_id', 'created_at'])

    op.create_table(
        'sso_connections',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('protocol', sa.String(length=10), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('display_name', sa.String(length=120), nullable=True),
        sa.Column('oidc_issuer', sa.String(length=2048), nullable=True),
        sa.Column('oidc_client_id', sa.String(length=255), nullable=True),
        sa.Column('oidc_client_secret', sa.Text(), nullable=True),
        sa.Column('saml_entity_id', sa.String(length=2048), nullable=True),
        sa.Column('saml_sso_url', sa.String(length=2048), nullable=True),
        sa.Column('saml_x509_cert', sa.Text(), nullable=True),
        sa.Column('jit_enabled', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('default_role_key', sa.String(length=50), server_default='agent', nullable=False),
        sa.Column('allowed_domains', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('attribute_mapping', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('status', sa.String(length=16), server_default='unconfigured', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', name='uq_sso_connections_org'),
        sa.CheckConstraint("protocol IN ('saml', 'oidc')", name='ck_sso_connections_protocol'),
    )

    op.create_table(
        'feature_flags',
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('updated_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'key', name='uq_feature_flags_org_key'),
    )
    op.create_index('ix_feature_flags_org', 'feature_flags', ['organization_id'])

    for table in _TABLES:
        for statement in tenant_policy_statements(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_TABLES):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_feature_flags_org', table_name='feature_flags')
    op.drop_table('feature_flags')
    op.drop_table('sso_connections')
    op.drop_index('ix_data_requests_org', table_name='data_requests')
    op.drop_table('data_requests')
    op.drop_table('compliance_policies')
    op.drop_table('organization_branding')
    op.drop_table('security_policies')
