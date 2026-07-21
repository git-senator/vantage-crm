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
