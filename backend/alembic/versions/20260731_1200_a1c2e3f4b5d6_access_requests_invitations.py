"""access requests and invitations

Self-service onboarding: a prospective member submits a request from the public
login page, a reviewer with `users.manage` approves it, and the admitted person
sets their own password through a single-use invitation link.

Both tables are deliberately **tenant-less** and therefore carry *no* RLS
policy — unlike every business table. A request-to-join belongs to no
organization until it is approved; an invitation must be resolvable from its
token before any tenant context exists. Access is guarded instead by the
`users.manage` permission (the review queue) and by the unguessable token (the
accept link). See app/models/access_request.py and app/models/invitation.py.

Revision ID: a1c2e3f4b5d6
Revises: f3b5c7d9e1a3
Created: 2026-07-31 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c2e3f4b5d6"
down_revision: str | None = "f3b5c7d9e1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_requests",
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("requested_role", sa.String(length=60), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default="pending", nullable=False
        ),
        sa.Column("reviewed_by_id", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_access_requests_status",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_access_requests_status_created",
        "access_requests",
        ["status", "created_at"],
        unique=False,
    )

    op.create_table(
        "invitations",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("role_key", sa.String(length=60), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column("access_request_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["access_request_id"], ["access_requests.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )
    op.create_index("ix_invitations_user", "invitations", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_invitations_user", table_name="invitations")
    op.drop_table("invitations")
    op.drop_index(
        "ix_access_requests_status_created", table_name="access_requests"
    )
    op.drop_table("access_requests")
