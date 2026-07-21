"""rbac: add the contacts.assign permission

Phase 2.4. Clients need the same separation leads already have: editing a record
and handing it to a colleague are different privileges, so an agent can work
their own book without being able to move work onto someone else.

Data, not schema, and therefore a separate revision from the `clients` table —
each is independently reversible.

Idempotent by construction. A database created *after* this commit seeds
`contacts.assign` during d7305fe801ac, because that migration reads the same
registry this one does; only a database already at head needs this. Both cases
must converge, hence ON CONFLICT ... DO UPDATE throughout.

Grants are read from `SYSTEM_ROLES` rather than written out here, so the
migration cannot drift from the registry: owner and admin hold every
permission at `all`, manager gets `team`, agent is not granted it.

Revision ID: f7a2b8c1d3e4
Revises: e4f1a2b3c5d6
Created: 2026-07-21 10:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.permissions import PERMISSIONS_BY_KEY, SYSTEM_ROLES

revision: str = 'f7a2b8c1d3e4'
down_revision: str | None = 'e4f1a2b3c5d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION_KEY = "contacts.assign"


def upgrade() -> None:
    connection = op.get_bind()
    permission = PERMISSIONS_BY_KEY[PERMISSION_KEY]

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

    # Only system roles (organization_id IS NULL) are touched. A workspace's
    # custom roles are its own to configure — silently granting a new
    # permission into a customer-defined role would be a privilege escalation
    # nobody asked for.
    for role in SYSTEM_ROLES:
        scope = role.grants.get(PERMISSION_KEY)
        if scope is None:
            continue
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
                "permission_key": PERMISSION_KEY,
                "scope": scope.value,
            },
        )


def downgrade() -> None:
    connection = op.get_bind()

    # Grants first: role_permissions references permissions, and relying on a
    # cascade to clean up a privilege row is not something to leave implicit.
    connection.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE permission_id = (SELECT id FROM permissions WHERE key = :key)
            """
        ),
        {"key": PERMISSION_KEY},
    )
    connection.execute(
        sa.text("DELETE FROM permissions WHERE key = :key"),
        {"key": PERMISSION_KEY},
    )
