"""Task — a piece of work with a due date.

The last CRM entity, and the one with the loosest coupling: a task may hang off
any entity, or off none at all ("call the title company back" is a real task
with no record attached).

Two things differ from the entities before it:

  * **The scope anchor is `assignee_id`, not `owner_id`.** A task belongs to
    whoever has to do it. `created_by` is who asked — a manager assigning work
    must not lose sight of it, which is what makes the TEAM scope matter here.
  * **`status` is stored, not derived.** Unlike a deal, nothing else determines
    whether a task is done; there is no stage to read it from.

The polymorphic link uses the same (`entity_type`, `entity_id`) shape as
`activities`, and the same CHECK-constrained vocabulary, so the two can be
rendered on one timeline without a translation layer.
"""

from __future__ import annotations

from datetime import datetime
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

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User

TASK_STATUSES = ("todo", "in_progress", "blocked", "done")
TASK_PRIORITIES = ("low", "medium", "high", "urgent")

#: Entities a task can hang off. Kept identical to ACTIVITY_ENTITY_TYPES so a
#: unified timeline needs no mapping between the two vocabularies.
TASK_ENTITY_TYPES = ("lead", "client", "property", "deal")


class Task(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tasks"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # The RBAC scope anchor: a task belongs to whoever has to do it.
    assignee_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="todo", server_default="todo"
    )
    priority: Mapped[str] = mapped_column(
        String(10), nullable=False, default="medium", server_default="medium"
    )

    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set by the complete action, cleared on reopen. Never accepted on write —
    #: a completed_at that disagrees with status is unanswerable.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Optional polymorphic link. Both null, or both set — enforced by
    # ck_tasks_entity_pair, because half a reference points nowhere.
    entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )

    created_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', "
            "coalesce(title, '') || ' ' || "
            "coalesce(description, ''))",
            persisted=True,
        ),
        nullable=False,
    )

    assignee: Mapped[User | None] = relationship(
        foreign_keys=[assignee_id], lazy="joined"
    )

    __table_args__ = (
        CheckConstraint("length(title) > 0", name="ck_tasks_title"),
        CheckConstraint(
            "status IN ('todo', 'in_progress', 'blocked', 'done')",
            name="ck_tasks_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'urgent')",
            name="ck_tasks_priority",
        ),
        CheckConstraint(
            "entity_type IS NULL OR "
            "entity_type IN ('lead', 'client', 'property', 'deal')",
            name="ck_tasks_entity_type",
        ),
        # Half a polymorphic reference points nowhere and is impossible to
        # query — either both columns are set, or neither is.
        CheckConstraint(
            "(entity_type IS NULL) = (entity_id IS NULL)",
            name="ck_tasks_entity_pair",
        ),
        # `done` and a null completed_at disagree with each other, and every
        # "how long did this take" question depends on the pair being coherent.
        CheckConstraint(
            "(status = 'done') = (completed_at IS NOT NULL)",
            name="ck_tasks_completed_at",
        ),
        # The default list: my open tasks, soonest first. `due_at NULLS LAST`
        # is expressed in the query; the index serves the ordering either way.
        Index(
            "ix_tasks_org_assignee_due",
            "organization_id",
            "assignee_id",
            "due_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Keyset pagination orders by (created_at, id).
        Index(
            "ix_tasks_org_created_id",
            "organization_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_tasks_org_status",
            "organization_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # An entity's task list, which the unified timeline reads per record.
        Index(
            "ix_tasks_entity",
            "organization_id",
            "entity_type",
            "entity_id",
            postgresql_where=text("entity_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index("ix_tasks_search", "search_vector", postgresql_using="gin"),
        # The repository falls back to trigram similarity on title when the
        # tsquery misses — same pattern as every other searchable entity, and
        # the `%` operator is a sequential scan without this.
        Index(
            "ix_tasks_title_trgm",
            text("title gin_trgm_ops"),
            postgresql_using="gin",
        ),
    )

    @property
    def is_done(self) -> bool:
        return self.status == "done"

    @property
    def is_overdue(self) -> bool:
        """Past due and not finished.

        Derived rather than stored: a stored flag is correct until the clock
        moves, which is to say almost never.
        """
        if self.due_at is None or self.is_done:
            return False
        from datetime import UTC

        return self.due_at < datetime.now(UTC)

    def __repr__(self) -> str:
        return f"<Task {self.id}>"
