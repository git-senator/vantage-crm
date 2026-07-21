"""Note — a rich-text annotation pinned to a record.

A note is not an activity, and the distinction is the reason it is a separate
table rather than an `activity` of type `note`:

  * an **activity** is a timeline *event* — something happened at `occurred_at`,
    and the entry is a terse log line ("Called, left voicemail");
  * a **note** is a *document* — a durable, editable, formatted piece of
    writing about the record ("Buyer's financing notes", a paragraph of
    context), that a person comes back to and revises.

Both surface on the unified timeline, which is why the polymorphic
(`entity_type`, `entity_id`) pair is byte-for-byte the shape `activities` and
`tasks` use — one timeline needs no translation between the three.

Two properties specific to notes:

  * **A note is always about a record.** `entity_type`/`entity_id` are NOT NULL,
    unlike a task ("call the title company back" is a real standalone task; a
    note about nothing is not a real note). The vocabulary adds `task` — a note
    can annotate a task, which activities cannot.
  * **Visibility follows the parent, editing follows authorship.** A note has
    no scope anchor of its own; whoever can read the record reads its notes
    (resolved through `EntityAccess`), and only the author — or someone at ALL
    scope — may edit or delete. This mirrors `activities` exactly.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
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

#: How the body is authored. `markdown` is the default and the safe path — it
#: is rendered client-side and never trusted as HTML. `html` exists for a
#: future WYSIWYG that emits sanitised markup; `plain` is an escape hatch.
NOTE_CONTENT_FORMATS = ("markdown", "html", "plain")

#: Entities a note can hang off. A superset of ACTIVITY_ENTITY_TYPES — a note
#: may annotate a task, which an activity may not.
NOTE_ENTITY_TYPES = ("lead", "client", "property", "deal", "task")


class Note(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "notes"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Who wrote it, and the anchor for edit/delete authorization. SET NULL so
    #: an author leaving does not erase the record's notes.
    author_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Always both set — a note is always about a record. NOT NULL, and the
    # CHECK constrains the vocabulary because a polymorphic reference has no FK.
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=False
    )

    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_format: Mapped[str] = mapped_column(
        String(10), nullable=False, default="markdown", server_default="markdown"
    )

    #: Pinned notes sort to the top of a record's note list — the "read me
    #: first" context an agent wants before every call.
    is_pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
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
            "coalesce(body, ''))",
            persisted=True,
        ),
        nullable=False,
    )

    author: Mapped[User | None] = relationship(
        foreign_keys=[author_id], lazy="joined"
    )

    __table_args__ = (
        CheckConstraint("length(body) > 0", name="ck_notes_body"),
        CheckConstraint(
            "entity_type IN ('lead', 'client', 'property', 'deal', 'task')",
            name="ck_notes_entity_type",
        ),
        CheckConstraint(
            "content_format IN ('markdown', 'html', 'plain')",
            name="ck_notes_content_format",
        ),
        # A record's notes, pinned first then newest — the per-entity read the
        # detail page and the timeline both make.
        Index(
            "ix_notes_entity",
            "organization_id",
            "entity_type",
            "entity_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Keyset pagination for the cross-entity feed orders by (created_at, id).
        Index(
            "ix_notes_org_created_id",
            "organization_id",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # "My notes" — the author-scoped feed.
        Index(
            "ix_notes_org_author",
            "organization_id",
            "author_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_notes_search", "search_vector", postgresql_using="gin"),
        # Trigram fallback on title, same as every searchable entity, so the
        # `%` operator does not degrade to a sequential scan.
        Index(
            "ix_notes_title_trgm",
            text("coalesce(title, '') gin_trgm_ops"),
            postgresql_using="gin",
        ),
    )

    def __repr__(self) -> str:
        return f"<Note {self.id}>"
