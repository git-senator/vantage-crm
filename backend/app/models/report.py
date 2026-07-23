"""Saved reports and their run history.

Two tables with deliberately different lifetimes.

`report_definitions` is a **document the user authored** — soft-deleted, edited
in place, versionless. It stores the specification as JSONB rather than as
columns because the shape is genuinely open (a list of filters, a list of
aggregates) and normalising it would produce four tables nobody queries
independently. The JSONB is never trusted: it is re-validated against the
registry on every execution, not only on save, because a definition written last
month may reference a field the registry has since dropped.

`report_runs` is an **operational record** — append-only, never edited. It exists
so "who exported the client list, when, and how many rows" has an answer. That
question is asked after an incident, which is why the row survives the deletion
of the definition it came from (`ON DELETE SET NULL`, not CASCADE): losing the
evidence when someone deletes the report is exactly backwards.

A run holds a `storage_key`, not bytes. The file lives in object storage like
every other document, and is served through the same short-lived signed URL —
routing a 40 MB spreadsheet through the API process is the mistake presign-first
storage exists to avoid.
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
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

#: A run's lifecycle. `partial` is a success that hit the row cap — distinct
#: from `succeeded` because a truncated export that claims to be complete is the
#: failure mode this whole column exists to prevent.
RUN_STATUSES = ("queued", "running", "succeeded", "partial", "failed")

#: How often a saved report re-runs on its own. Coarse on purpose: a report is a
#: digest, and cron-level expressiveness invites schedules nobody can reason
#: about from a list view.
SCHEDULES = ("none", "daily", "weekly", "monthly")


class ReportDefinition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "report_definitions"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Who authored it. Scope is resolved on this column, so a report is private
    #: to its author unless shared — a saved report can encode somebody's
    #: commission assumptions, and those are not team property by default.
    owner_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Denormalised out of `definition` so the list view can filter and group by
    #: it without reading JSONB — the same reason `workflow_versions` lifts
    #: `trigger_type` out of its definition blob.
    dataset: Mapped[str] = mapped_column(String(40), nullable=False)

    #: The specification. Re-validated on every run; see the module docstring.
    definition: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )

    #: Visible to everyone in the workspace who can see the underlying dataset.
    #: Sharing widens who may *run* the report; it never widens the rows, which
    #: are always resolved against the runner's own scope.
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    schedule: Mapped[str] = mapped_column(String(20), nullable=False, server_default="none")
    #: Format a scheduled run produces.
    schedule_format: Mapped[str] = mapped_column(String(10), nullable=False, server_default="xlsx")
    #: Who gets told when a scheduled run finishes. User ids, not email
    #: addresses: a report is delivered as a notification with a signed link, so
    #: an ex-employee's address cannot keep receiving the company's numbers.
    recipients: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
        nullable=False,
        server_default="{}",
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    __table_args__ = (
        CheckConstraint("length(name) > 0", name="ck_report_definitions_name"),
        CheckConstraint(
            "schedule IN ('none', 'daily', 'weekly', 'monthly')",
            name="ck_report_definitions_schedule",
        ),
        CheckConstraint(
            "schedule_format IN ('csv', 'xlsx', 'pdf')",
            name="ck_report_definitions_format",
        ),
        UniqueConstraint("organization_id", "name", name="uq_report_definitions_name"),
        Index(
            "ix_report_definitions_org",
            "organization_id",
            "created_at",
            postgresql_where="deleted_at IS NULL",
        ),
        # The scheduler's query, and normally empty. Partial so the sweep costs
        # nothing in a workspace with no scheduled reports — which is most of
        # them, most of the time.
        Index(
            "ix_report_definitions_scheduled",
            "schedule",
            "last_run_at",
            postgresql_where="schedule <> 'none' AND deleted_at IS NULL",
        ),
    )

    def __repr__(self) -> str:
        return f"<ReportDefinition {self.name} ({self.dataset})>"


class ReportRun(Base, UUIDPrimaryKeyMixin):
    """One execution. Append-only; never updated after it terminates."""

    __tablename__ = "report_runs"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: SET NULL, not CASCADE. Deleting the report must not delete the record
    #: that somebody exported eight thousand client rows from it.
    definition_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("report_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: NULL for a scheduled run: there is no user behind a cron tick, and faking
    #: one with a placeholder id is how an audit trail starts lying.
    requested_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Copied from the definition at run time, so history survives an edit. A
    #: run that says "1,204 rows" against a definition since rewritten is
    #: otherwise unexplainable.
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset: Mapped[str] = mapped_column(String(40), nullable=False)
    definition_snapshot: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )

    format: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    is_scheduled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: What the report *would* have returned. Differs from `row_count` exactly
    #: when the run was truncated, which is what makes `partial` checkable.
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'failed')",
            name="ck_report_runs_status",
        ),
        CheckConstraint("format IN ('csv', 'xlsx', 'pdf')", name="ck_report_runs_format"),
        Index("ix_report_runs_org", "organization_id", "started_at"),
        Index("ix_report_runs_definition", "definition_id", "started_at"),
    )

    def __repr__(self) -> str:
        return f"<ReportRun {self.name} {self.status} {self.row_count} rows>"
