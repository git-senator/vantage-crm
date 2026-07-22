"""User account.

Note `email` is CITEXT, not TEXT. A unique index on a plain TEXT email column
lets `Bob@example.com` and `bob@example.com` both register, which is an account
takeover vector as much as a data quality problem.

Uniqueness is scoped to the organization rather than global, so the same person
can belong to two tenants once multi-tenancy is activated.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.refresh_token import RefreshToken

# Lifecycle, not permission. A suspended user keeps their roles.
USER_STATUSES = ("active", "invited", "suspended", "deactivated")


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    # Declared explicitly rather than via TenantMixin: the mixin's composite
    # index is tuned for high-volume CRM tables, and users needs a different
    # index set (see __table_args__).
    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Drives the generated initials avatar in the existing UI, which uses a
    # stored hue rather than image assets. Keeping the column means the
    # frontend avatar component needs no change.
    avatar_hue: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=268)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    # ------------------------------------------------------ auth state
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Brute-force throttling. Counted per account, independently of the
    # per-IP limit, so a distributed attack on one account is still caught.
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ------------------------------------------------------------- MFA
    #
    # `mfa_enabled` is the activated flag, not the enrolled one. A secret is
    # written at enrolment and the flag flips only once the user has proved a
    # code from it — otherwise a half-finished setup locks somebody out of
    # their own account with a secret they never scanned.
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: Base32 TOTP secret.
    #:
    #: Stored as issued, and that is a stated trade-off rather than an
    #: oversight: encryption at rest for this column needs a key managed
    #: somewhere other than beside the data — a KMS — which is Phase 4's
    #: secrets work. What is in place meanwhile is that the column is in
    #: `NEVER_DIFF_FIELDS`, so it cannot reach the audit log, is excluded from
    #: every read schema, and the logger's redaction list covers it. The
    #: residual exposure is a database dump, which is the same exposure the
    #: password hashes have.
    mfa_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The last TOTP counter accepted for this user. A code stays valid for its
    #: whole 30-second step, so without this an observed code can be replayed
    #: inside it. See app/core/totp.py.
    mfa_last_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # ---------------------------------------------------- relationships
    organization: Mapped[Organization] = relationship(back_populates="users")
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Per-organization uniqueness, not global.
        UniqueConstraint("organization_id", "email", name="uq_users_org_email"),
        CheckConstraint(
            "status IN ('active', 'invited', 'suspended', 'deactivated')",
            name="ck_users_status",
        ),
        CheckConstraint(
            "avatar_hue >= 0 AND avatar_hue < 360", name="ck_users_avatar_hue"
        ),
        CheckConstraint("failed_login_count >= 0", name="ck_users_failed_login_count"),
        # Login looks up by email within an org and ignores deleted rows; this
        # partial index serves exactly that path.
        Index(
            "ix_users_org_email_active",
            "organization_id",
            "email",
            postgresql_where="deleted_at IS NULL",
        ),
    )

    @property
    def initials(self) -> str:
        """Two-letter initials for the avatar component."""
        parts = [p for p in self.full_name.replace("&", " ").split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    @property
    def is_active(self) -> bool:
        return self.status == "active" and self.deleted_at is None

    def __repr__(self) -> str:
        # Deliberately excludes email — reprs end up in logs and tracebacks.
        return f"<User {self.id}>"
