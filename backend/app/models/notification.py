"""Notifications — what a person is told, and how they chose to be told it.

Two tables, and the split is the design.

`notifications` is the **in-app record**: one row per recipient per event. It is
deliberately not a copy of the audit log and not a copy of the timeline —
the timeline says what happened to a *record*, the audit log says what happened
for *compliance*, and this says what a *person* still needs to look at. Those
three answer different questions and diverge immediately: an activity nobody
needs to act on belongs on the timeline and not here, and a notification is read
and dismissed while a timeline entry is permanent.

`notification_preferences` is per user, per category, per channel. Absent means
"the default for that category" rather than "off" — a preference row is created
only when someone changes something, so adding a category later does not
silently mute it for every existing user.

**Scope anchors on the recipient, not an owner.** A notification about a lead is
not visible to everyone who can see the lead — it belongs to the one person it
was sent to. That predicate lives in the repository, not in the RLS policy,
following the split the rest of the system uses: **RLS is the tenant boundary;
row visibility is a SQL predicate from the scope resolver** (docs/SECURITY.md
§1). Pushing recipient scoping into the policy would also break the writers,
since a notification is created by one user *for another* — a `WITH CHECK` on
the recipient would refuse every assignment notification ever sent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User

#: Broad buckets a person would actually want to mute independently. Kept
#: coarse on purpose: a preference screen with thirty switches is one nobody
#: configures, and the finer distinction lives in `type`.
NOTIFICATION_CATEGORIES = ("lead", "deal", "task", "document", "mention", "system")

#: Where a notification can be delivered. `in_app` is always written — it is the
#: record of the event; the preference governs whether it is *surfaced* as
#: unread. `email` is queued.
NOTIFICATION_CHANNELS = ("in_app", "email")


class Notification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "notifications"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: The person being told. CASCADE rather than SET NULL: a notification with
    #: no recipient is not a record worth keeping — it is unreachable by
    #: definition, and the audit log holds what actually happened.
    user_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Who caused it. NULL for machine-generated notifications, which is what
    #: distinguishes "the scanner quarantined your upload" from "Sofia
    #: mentioned you".
    actor_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    category: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The specific event, e.g. `task.assigned`. Finer than `category`, which is
    #: what preferences switch on.
    type: Mapped[str] = mapped_column(String(60), nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: What to open when it is clicked. Polymorphic like notes and activities.
    entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )

    #: Free-form context for rendering. Never PII beyond what the title and body
    #: already carry — this is serialised to the client verbatim.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )

    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When the email went out, if it did. Null means it was not sent — either
    #: preferences said not to, or delivery has not happened yet.
    emailed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    actor: Mapped[User | None] = relationship(foreign_keys=[actor_id], lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "category IN ('lead', 'deal', 'task', 'document', 'mention', 'system')",
            name="ck_notifications_category",
        ),
        CheckConstraint("length(title) > 0", name="ck_notifications_title"),
        # The bell's query: this user's unread, newest first. Partial, because
        # unread is a small and shrinking subset of a table that only grows.
        Index(
            "ix_notifications_unread",
            "organization_id",
            "user_id",
            "created_at",
            postgresql_where=text("read_at IS NULL"),
        ),
        # The list's query: this user's notifications, newest first.
        Index(
            "ix_notifications_user_created",
            "organization_id",
            "user_id",
            "created_at",
            "id",
        ),
    )

    def __repr__(self) -> str:
        return f"<Notification {self.type} -> {self.user_id}>"


class NotificationPreference(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per (user, category). Absent means "use the default"."""

    __tablename__ = "notification_preferences"

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
    category: Mapped[str] = mapped_column(String(20), nullable=False)

    in_app: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    email: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('lead', 'deal', 'task', 'document', 'mention', 'system')",
            name="ck_notification_preferences_category",
        ),
        UniqueConstraint(
            "user_id", "category", name="uq_notification_preferences_user_category"
        ),
        Index(
            "ix_notification_preferences_user",
            "organization_id",
            "user_id",
        ),
    )

    def __repr__(self) -> str:
        return f"<NotificationPreference {self.user_id} {self.category}>"
