"""Refresh token with family lineage for rotation and reuse detection.

Every refresh issues a new token and marks the presented one used. Tokens
descending from one login share a `family_id`.

If an already-used token is presented, that means two parties hold the same
token — the legitimate user and a thief. Which one is which is unknowable, so
the entire family is revoked and both must re-authenticate. Detecting theft is
the reason rotation is worth doing at all.

See docs/SECURITY.md §2.3.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class RefreshToken(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # All tokens descended from a single login. Revoking a family logs out that
    # session lineage without touching the user's other devices.
    family_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)

    # SHA-256 of the raw token. The raw value exists only in the cookie — a
    # database disclosure yields no usable sessions.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Set on first use. A second presentation is the theft signal.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # Forward link to the token that replaced this one; makes a family's
    # lineage walkable during incident review.
    replaced_by_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Captured for forensics. Not used for authorization: both are spoofable,
    # and binding a session to them breaks legitimate users on mobile networks.
    ip_address: Mapped[str | None] = mapped_column(postgresql.INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    __table_args__ = (
        # Revoking a whole family on reuse detection.
        Index("ix_refresh_tokens_family", "family_id"),
        # Listing a user's live sessions, and the expiry sweep.
        Index("ix_refresh_tokens_user_expiry", "user_id", "expires_at"),
        Index("ix_refresh_tokens_org", "organization_id"),
    )

    @property
    def is_usable(self) -> bool:
        """Never been used, not revoked. Expiry is checked against a clock."""
        return self.used_at is None and self.revoked_at is None

    def __repr__(self) -> str:
        # Never include token_hash.
        return f"<RefreshToken {self.id} family={self.family_id}>"
