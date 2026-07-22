"""JobFailure — the dead-letter record.

A queue that drops a job after its last retry and says nothing is a queue you
cannot operate. The worker logs the failure, but a log line is not a view: it
cannot be filtered by tenant, it disappears with retention, and nobody notices
one at 3 a.m. This table is what makes "which jobs are failing, on whose data,
and since when" a question with an answer.

**One row per (job name, key), not per attempt.** A job failing every ten
minutes for a week is one problem, and recording 1 008 rows for it buries the
other three problems underneath. `attempts` counts, `first_failed_at` and
`last_failed_at` bracket, and `resolved_at` closes it when a later run succeeds.

**`organization_id` is nullable** because not every job belongs to a tenant —
the scheduler's own sweep does not. The RLS policy is correspondingly a shade
wider than the standard one: a NULL-tenant row is infrastructure, visible to
any admin who can see the view at all, and it holds no customer data by
construction. Tenant rows obey the usual rule.

**No payload is stored.** `job_args` holds ids and job parameters only, and the
error is reduced to a class name and a message. A dead-letter table that
recorded arguments verbatim would become the one place customer data outlives
its retention policy.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class JobFailure(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "job_failures"

    organization_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )

    job_name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: What the job was acting on — an attachment id, a user id. Combined with
    #: `job_name` this is the identity of the *problem*, which is why repeated
    #: failures of the same job on the same target collapse into one row.
    #: Empty string rather than NULL for jobs with no target, so the unique
    #: index actually constrains them (NULLs never collide in Postgres).
    job_key: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", server_default=""
    )

    job_args: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )

    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    error_class: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Truncated before storage. A stack trace belongs in the log, where it is
    #: correlated with everything else that happened in that job.
    error_message: Mapped[str] = mapped_column(Text, nullable=False)

    first_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Set when a later run of the same job/key succeeds. The row is kept —
    #: "this was broken for six hours on Tuesday" is worth being able to ask.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("attempts > 0", name="ck_job_failures_attempts"),
        UniqueConstraint("job_name", "job_key", name="uq_job_failures_identity"),
        # The dead-letter view's query: unresolved, newest first.
        Index(
            "ix_job_failures_open",
            "last_failed_at",
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index("ix_job_failures_org", "organization_id", "last_failed_at"),
    )

    def __repr__(self) -> str:
        return f"<JobFailure {self.job_name} {self.job_key or '-'}>"
