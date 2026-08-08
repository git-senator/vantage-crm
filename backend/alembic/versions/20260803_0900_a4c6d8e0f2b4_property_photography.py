"""property photography — properties.cover_attachment_id, attachments.sort_order

Listings gain photography. Photos are ordinary attachments filed against the
listing (``entity_type='property'``), which the attachments table has always
supported, so only two things were missing.

``properties.cover_attachment_id`` — which photo leads. A nullable FK with ON
DELETE SET NULL: deleting a photo demotes the listing to its generated gradient
rather than taking the listing with it.

``attachments.sort_order`` — the order within a record. Documents have never
needed one (newest-first is right for them, hence the 0 default), but a gallery
written in a single transaction shares ``created_at`` to the microsecond across
every row, so ordering by timestamp alone breaks ties on a random UUID and
shuffles the sequence someone arranged.

Revision ID: a4c6d8e0f2b4
Revises: c3e5a7b9d1f2
Created: 2026-08-03 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a4c6d8e0f2b4'
down_revision: str | None = 'c3e5a7b9d1f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column(
            "sort_order",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "properties",
        sa.Column(
            "cover_attachment_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_properties_cover_attachment",
        "properties",
        "attachments",
        ["cover_attachment_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_properties_cover_attachment", "properties", type_="foreignkey"
    )
    op.drop_column("properties", "cover_attachment_id")
    op.drop_column("attachments", "sort_order")
