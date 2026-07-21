"""rbac: roles, permissions, teams

Phase 1.3. Adds the authorization tables and seeds them from
`app.core.permissions`, which is the single source of truth for the permission
vocabulary and the built-in roles.

Seeding here rather than in application startup means a fresh database is
immediately usable and the seed is versioned with the schema that supports it.

Two tenant-scoped tables (user_roles, teams, team_members) get RLS policies;
`permissions` and system `roles` are deliberately global — the vocabulary
belongs to the application, not to a customer.

Revision ID: d7305fe801ac
Revises: c3d5e7f9a1b2
Created: 2026-07-21 02:16:04.326191

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.permissions import PERMISSIONS, SYSTEM_ROLES
from app.db.sql_objects import drop_tenant_policy_statements, tenant_policy_statements

revision: str = 'd7305fe801ac'
down_revision: str | None = 'c3d5e7f9a1b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('permissions',
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('resource', sa.String(length=40), nullable=False),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('description', sa.String(length=200), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint("key = resource || '.' || action", name='ck_permissions_key'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_index('ix_permissions_resource', 'permissions', ['resource'], unique=False)
    op.create_table('roles',
    sa.Column('organization_id', sa.UUID(), nullable=True),
    sa.Column('key', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=False),
    sa.Column('is_system', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_protected', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name='ck_roles_key_format'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'key', name='uq_roles_org_key', postgresql_nulls_not_distinct=True)
    )
    op.create_index(op.f('ix_roles_organization_id'), 'roles', ['organization_id'], unique=False)
    op.create_table('role_permissions',
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.Column('permission_id', sa.UUID(), nullable=False),
    sa.Column('scope', sa.String(length=10), nullable=False),
    sa.CheckConstraint("scope IN ('own', 'team', 'all')", name='ck_role_permissions_scope'),
    sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('role_id', 'permission_id')
    )
    op.create_table('teams',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('lead_user_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['lead_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'name', name='uq_teams_org_name')
    )
    op.create_index(op.f('ix_teams_organization_id'), 'teams', ['organization_id'], unique=False)
    op.create_table('user_roles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('granted_by', sa.UUID(), nullable=True),
    sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['granted_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'role_id')
    )
    op.create_index(op.f('ix_user_roles_organization_id'), 'user_roles', ['organization_id'], unique=False)
    op.create_index('ix_user_roles_user', 'user_roles', ['user_id'], unique=False)
    op.create_table('team_members',
    sa.Column('team_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('team_id', 'user_id')
    )
    op.create_index(op.f('ix_team_members_organization_id'), 'team_members', ['organization_id'], unique=False)
    op.create_index('ix_team_members_user', 'team_members', ['user_id'], unique=False)


    # ------------------------------------------------------------ seed
    _seed_permissions_and_roles()

    # ------------------------------------------------------------- RLS
    # user_roles, teams and team_members are tenant-scoped. Without policies
    # here, a role assignment would be the one table outside the tenant
    # boundary — and role assignments are exactly what an attacker would
    # target.
    for statement in tenant_policy_statements("user_roles"):
        op.execute(statement)
    for statement in tenant_policy_statements("teams"):
        op.execute(statement)
    for statement in tenant_policy_statements("team_members"):
        op.execute(statement)


def _seed_permissions_and_roles() -> None:
    """Insert the permission vocabulary and the four built-in roles.

    Idempotent: ON CONFLICT DO NOTHING/UPDATE so re-running against a partially
    seeded database converges rather than failing.
    """
    connection = op.get_bind()

    # --- permissions -------------------------------------------------
    for permission in PERMISSIONS:
        connection.execute(
            sa.text(
                """
                INSERT INTO permissions (id, key, resource, action, description)
                VALUES (gen_random_uuid(), :key, :resource, :action, :description)
                ON CONFLICT (key) DO UPDATE
                    SET description = EXCLUDED.description
                """
            ),
            {
                "key": permission.key,
                "resource": permission.resource,
                "action": permission.action,
                "description": permission.description,
            },
        )

    # --- system roles ------------------------------------------------
    # organization_id IS NULL marks a role shared by every workspace.
    for role in SYSTEM_ROLES:
        connection.execute(
            sa.text(
                """
                INSERT INTO roles (
                    id, organization_id, key, name, description,
                    is_system, is_protected, created_at, updated_at
                )
                VALUES (
                    gen_random_uuid(), NULL, :key, :name, :description,
                    true, :is_protected, now(), now()
                )
                ON CONFLICT (organization_id, key) DO UPDATE
                    SET name = EXCLUDED.name,
                        description = EXCLUDED.description
                """
            ),
            {
                "key": role.key,
                "name": role.name,
                "description": role.description,
                "is_protected": role.is_protected,
            },
        )

        # Replace grants wholesale so a role's definition here is authoritative
        # and a removed grant actually disappears.
        connection.execute(
            sa.text(
                """
                DELETE FROM role_permissions
                WHERE role_id = (
                    SELECT id FROM roles
                    WHERE key = :key AND organization_id IS NULL
                )
                """
            ),
            {"key": role.key},
        )

        for permission_key, scope in role.grants.items():
            connection.execute(
                sa.text(
                    """
                    INSERT INTO role_permissions (role_id, permission_id, scope)
                    SELECT r.id, p.id, :scope
                    FROM roles r, permissions p
                    WHERE r.key = :role_key AND r.organization_id IS NULL
                      AND p.key = :permission_key
                    ON CONFLICT (role_id, permission_id) DO UPDATE
                        SET scope = EXCLUDED.scope
                    """
                ),
                {
                    "role_key": role.key,
                    "permission_key": permission_key,
                    "scope": scope.value,
                },
            )


def downgrade() -> None:
    for table in ("team_members", "teams", "user_roles"):
        for statement in drop_tenant_policy_statements(table):
            op.execute(statement)

    op.drop_index('ix_team_members_user', table_name='team_members')
    op.drop_index(op.f('ix_team_members_organization_id'), table_name='team_members')
    op.drop_table('team_members')
    op.drop_index('ix_user_roles_user', table_name='user_roles')
    op.drop_index(op.f('ix_user_roles_organization_id'), table_name='user_roles')
    op.drop_table('user_roles')
    op.drop_index(op.f('ix_teams_organization_id'), table_name='teams')
    op.drop_table('teams')
    op.drop_table('role_permissions')
    op.drop_index(op.f('ix_roles_organization_id'), table_name='roles')
    op.drop_table('roles')
    op.drop_index('ix_permissions_resource', table_name='permissions')
    op.drop_table('permissions')
