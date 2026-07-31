"""Invitation — a single-use link that lets an admitted user set their password.

Like `refresh_tokens`, the raw token exists only in the emailed link; the table
stores its SHA-256. A database disclosure yields no usable invitations.

Also **tenant-less by necessity**: the accept-invite page is opened by someone
with no session, so the token must be resolvable before any tenant context is
bound. The row carries the `organization_id` and `user_id` it resolves to, and
the accept handler binds that context before touching the RLS-protected
`users` row. Access is by the unguessable token alone — there is no listing
endpoint — so no policy is needed.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class Invitation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "invitations"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The account waiting to be activated. Deleting the user voids the invite.
    user_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalised so the accept page can greet the person without a tenant-bound
    # read of the users table.
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    role_key: Mapped[str] = mapped_column(String(60), nullable=False)

    # SHA-256 of the raw token. Lookups query by this, never the raw value.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Set the moment the password is chosen. A second presentation is refused.
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Who approved the request, and the request it came from (for the trail).
    created_by_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    access_request_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("access_requests.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_invitations_user", "user_id"),
    )

    @property
    def is_usable(self) -> bool:
        """Never accepted. Expiry is checked separately against a clock."""
        return self.accepted_at is None

    def __repr__(self) -> str:
        # Never include token_hash.
        return f"<Invitation {self.id} org={self.organization_id}>"
