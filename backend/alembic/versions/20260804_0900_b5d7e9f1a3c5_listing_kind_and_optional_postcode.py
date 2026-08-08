"""listings for rent, and a postcode that may be absent

Two corrections that the Brazilian catalogue made unavoidable.

**`listing_kind` / `rent_period`.** A listing was implicitly always for sale, so
a rental's monthly figure sat in the same `price` column as a villa's asking
price. Nothing was wrong in the row; everything was wrong downstream — a filter
for "under 100 000" returned a villa's rent beside a studio's price, and any
average over the column mixed two different quantities. The period is required
on a rental and forbidden on a sale, written as an equivalence so neither half
can hold alone.

**`postal_code` becomes nullable.** It was NOT NULL, which meant a listing with
no postcode had to be given a placeholder — and a placeholder in a postcode
field is worse than a blank, because it prints on an export or a contract
looking like a real value. Brazilian developments are identified by
neighbourhood long before a CEP exists, so "absent" is a normal state and now
has a way to be recorded.

Nothing here rewrites data. Rows that already carry a placeholder keep it until
whatever wrote them writes them again.

Revision ID: b5d7e9f1a3c5
Revises: a4c6d8e0f2b4
Created: 2026-08-04 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b5d7e9f1a3c5'
down_revision: str | None = 'a4c6d8e0f2b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every existing listing is a sale — that was the only thing the model
    # could express, so the default is a statement of fact rather than a guess.
    op.add_column(
        "properties",
        sa.Column(
            "listing_kind",
            sa.String(length=10),
            nullable=False,
            server_default="sale",
        ),
    )
    op.add_column(
        "properties",
        sa.Column("rent_period", sa.String(length=10), nullable=True),
    )
    op.create_check_constraint(
        "ck_properties_listing_kind",
        "properties",
        "listing_kind IN ('sale', 'rent')",
    )
    op.create_check_constraint(
        "ck_properties_rent_period",
        "properties",
        "(listing_kind = 'rent') = (rent_period IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_properties_rent_period_value",
        "properties",
        "rent_period IS NULL OR rent_period IN ('month', 'week', 'day')",
    )

    op.alter_column("properties", "postal_code", nullable=True)


def downgrade() -> None:
    # Going back needs a value where there is none. A blank is the least
    # dishonest filler available under a NOT NULL that should not exist.
    op.execute("UPDATE properties SET postal_code = '' WHERE postal_code IS NULL")
    op.alter_column("properties", "postal_code", nullable=False)

    op.drop_constraint(
        "ck_properties_rent_period_value", "properties", type_="check"
    )
    op.drop_constraint("ck_properties_rent_period", "properties", type_="check")
    op.drop_constraint("ck_properties_listing_kind", "properties", type_="check")
    op.drop_column("properties", "rent_period")
    op.drop_column("properties", "listing_kind")
