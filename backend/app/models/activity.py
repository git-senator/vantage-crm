"""Activity — the user-facing business timeline.

Distinct from `audit_logs`, and the distinction matters:

  * `audit_logs` is a **security** record. Append-only at the grant level,
    admin-only to read, retains who-did-what for compliance. It records that a
    field changed.
  * `activities` is a **business** record. Agents read it, it is scoped like
    any other CRM data, and it records that something happened worth telling a
    colleague about — a call, a showing, a deal moving stage.

Collapsing them would mean either exposing the security log to every agent, or
making the timeline admin-only. Neither is acceptable, so there are two tables.

Polymorphic by (`entity_type`, `entity_id`) rather than a nullable FK per
entity: the timeline spans leads, clients, properties and deals, and a column
per entity means a migration every time a fifth is added. The trade-off is no
referential integrity on the target, which is why `entity_type` is CHECK-
constrained to a known vocabulary.

**Scope of this slice (2.6).** Only `stage_change` activities are written, by
the deal transition action. Manual logging and the other entities' timelines
land with the Activities slice; the table is created now because Deals needs
somewhere to write.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User

ACTIVITY_TYPES = (
    "call",
    "email",
    "meeting",
    "note",
    "showing",
    "stage_change",
)

#: Entities a timeline can hang off. CHECK-constrained because a polymorphic
#: reference has no foreign key to catch a typo.
ACTIVITY_ENTITY_TYPES = ("lead", "client", "property", "deal")


class Activity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "activities"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Who did it. SET NULL so an agent leaving does not erase the timeline.
    actor_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=False
    )

    type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: When it happened, which is not always when it was recorded — a call
    #: logged on Monday may have happened on Friday.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        postgresql.JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', "
            "coalesce(subject, '') || ' ' || "
            "coalesce(body, ''))",
            persisted=True,
        ),
        nullable=False,
    )

    actor: Mapped[User | None] = relationship(foreign_keys=[actor_id], lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "type IN ('call', 'email', 'meeting', 'note', 'showing', 'stage_change')",
            name="ck_activities_type",
        ),
        CheckConstraint(
            "entity_type IN ('lead', 'client', 'property', 'deal')",
            name="ck_activities_entity_type",
        ),
        CheckConstraint("length(subject) > 0", name="ck_activities_subject"),
        # The timeline query, exactly as docs/DATABASE.md specifies it: one
        # entity's activities, newest first.
        Index(
            "ix_activities_entity",
            "organization_id",
            "entity_type",
            "entity_id",
            text("occurred_at DESC"),
        ),
        Index("ix_activities_org_occurred", "organization_id", text("occurred_at DESC")),
        Index("ix_activities_search", "search_vector", postgresql_using="gin"),
        # The global feed is "what have I been doing" — actor-scoped, newest
        # first. Distinct from the per-entity index above.
        Index(
            "ix_activities_org_actor_occurred",
            "organization_id",
            "actor_id",
            text("occurred_at DESC"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Activity {self.id} {self.type}>"
