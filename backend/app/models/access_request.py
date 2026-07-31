"""Access request — a prospective member asking to be let in.

This table is deliberately **tenant-less**. A person filling in the public
"request access" form belongs to no organization yet — that is the whole point
of the request — so there is no `organization_id` to scope it by and no RLS
policy on it. It is written by an unauthenticated visitor and read only by a
member holding `users.manage`, which is the gate that protects it instead.

`organization_id` appears only once the request is *approved*: it records which
workspace the reviewer admitted the person into, for the audit trail. Until
then it is NULL.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

ACCESS_REQUEST_STATUSES = ("pending", "approved", "rejected")


class AccessRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "access_requests"

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # CITEXT for the same reason as users.email: two casings are one person.
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)

    # What the requester says they are (agent / broker / manager …). Free text,
    # not a role key — the reviewer chooses the real role on approval. This is
    # only a hint about who is asking.
    requested_role: Mapped[str | None] = mapped_column(String(60), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )

    # Set on review. `reviewed_by_id` and `organization_id` are plain FKs with
    # SET NULL so deleting the reviewer or (theoretically) the org never blocks
    # keeping the request row for history.
    reviewed_by_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_access_requests_status",
        ),
        # The admin list shows pending first, newest first — this serves it.
        Index("ix_access_requests_status_created", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AccessRequest {self.id} status={self.status}>"
