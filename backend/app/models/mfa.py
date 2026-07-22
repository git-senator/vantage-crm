"""Recovery codes for multi-factor authentication.

A table rather than a JSON column on `users`, for one reason that decides it:
**single use must be enforced by the database, not by application code.** A
recovery code is spent by an UPDATE with `used_at IS NULL` in its predicate, so
two concurrent attempts with the same code cannot both succeed. Rewriting a JSON
array read-modify-write style has a race that is invisible in testing and
exploitable in production.

Codes are stored hashed. SHA-256 rather than Argon2 — see `app/core/totp.py` for
why a server-generated 50-bit random string does not want a slow hash.

`used_at` is kept rather than the row deleted: "one of your recovery codes was
used on Tuesday" is exactly the signal that tells someone their phone was
compromised, and a deleted row cannot say it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class MfaRecoveryCode(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "mfa_recovery_codes"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # One row per (user, code). Regenerating replaces the whole set, so a
        # collision here would mean the generator repeated itself.
        UniqueConstraint("user_id", "code_hash", name="uq_mfa_recovery_user_code"),
        # The verification query: this user's unspent codes.
        Index(
            "ix_mfa_recovery_unused",
            "user_id",
            postgresql_where=text("used_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<MfaRecoveryCode {self.user_id} {'used' if self.used_at else 'unused'}>"
