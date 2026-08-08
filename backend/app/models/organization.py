"""Organization — the tenancy root.

Every business table carries `organization_id` and every RLS policy compares
against it. The MVP seeds exactly one row; multi-tenant activation is a
provisioning change, not a migration. See docs/DATABASE.md §2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # URL-safe identifier. Reserved for subdomain or path routing when
    # multi-tenancy is activated.
    slug: Mapped[str] = mapped_column(String(63), nullable=False, unique=True, index=True)

    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="standard")

    # Per-org configuration that does not warrant its own columns yet
    # (timezone, currency, feature flags).
    settings: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )

    users: Mapped[list[User]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )

    @property
    def measurement_system(self) -> str:
        """Which units this workspace reads areas in — `metric` or `imperial`.

        Areas are stored in square feet, because that is what the column is
        called and a column named `square_feet` holding square metres is a
        lie waiting to be found. Which unit a *person* sees is a workspace
        preference, resolved here so the session carries it and every page can
        format without another round trip.

        Metric is the default. The column's name is a North-American
        inheritance; most of the world, and every market this is sold into,
        measures property in square metres.
        """
        value = (self.settings or {}).get("measurement_system")
        return "imperial" if value == "imperial" else "metric"

    __table_args__ = (
        # Enforced in the database, not only in Pydantic: a slug written by a
        # script or a future admin tool must obey the same rule.
        CheckConstraint(
            "slug ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'",
            name="ck_organizations_slug_format",
        ),
        CheckConstraint("length(name) >= 2", name="ck_organizations_name_length"),
    )

    def __repr__(self) -> str:
        return f"<Organization {self.slug}>"
