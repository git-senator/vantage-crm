"""Workflows, their versions, their runs, and the event outbox that starts them.

Five tables. The shape is driven by three requirements that pull against each
other, and the comments below say which one each decision serves.

**A published workflow must be immutable while it runs.** Editing a live
automation under a half-finished run would let a customer receive the first half
of one workflow and the second half of another. So the editable thing and the
executable thing are different rows: `workflows` is the identity and the on/off
switch, `workflow_versions` holds the definition, and a run pins the version it
started on. Editing a published workflow creates a new draft.

**Matching an event to workflows must be an index lookup.** Every CRM mutation
emits an event, so the "which workflows care about this?" query runs constantly.
`workflow_versions.trigger_type` is therefore denormalised out of the definition
JSON — the alternative is a JSONB scan across every workflow in the tenant on
every single record edit.

**A trigger must not fire for a change that rolled back.** The event is written
to `workflow_events` inside the *caller's* transaction, so it commits atomically
with the change that caused it. A dispatcher picks it up afterwards. This is the
transactional outbox pattern, and it is the one place in this codebase where the
best-effort enqueue used everywhere else is not good enough: a phantom trigger
sends a real customer a real email about something that never happened.
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
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User

#: A version is edited, then frozen, then eventually superseded. `archived` is
#: the previous published version — kept because a run in flight still points at
#: it, and because "what was this automation doing last Tuesday" is a question
#: an operator asks after a customer complains.
WORKFLOW_VERSION_STATUSES = ("draft", "published", "archived")

#: `waiting` is the state that makes delays possible without holding a worker:
#: the run is parked with a `resume_at` and a sweep wakes it. Everything else is
#: the ordinary lifecycle.
WORKFLOW_RUN_STATUSES = (
    "pending",
    "running",
    "waiting",
    "succeeded",
    "failed",
    "cancelled",
)

WORKFLOW_STEP_STATUSES = ("succeeded", "failed", "skipped")


class Workflow(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """The identity and the switch. Holds no logic of its own."""

    __tablename__ = "workflows"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The switch is on the workflow, not the version, so pausing an automation
    #: does not require publishing anything — which is what you want at 2am
    #: when it is misbehaving.
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    #: Which version actually runs. NULL for a workflow that has only ever been
    #: a draft — a perfectly normal state, and the reason this is nullable.
    published_version_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        # No FK: `workflow_versions.workflow_id` already points back here, and a
        # second FK in the other direction makes both tables un-droppable
        # without a deferred constraint dance in every migration.
        nullable=True,
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

    author: Mapped[User | None] = relationship(
        foreign_keys=[created_by], lazy="joined"
    )
    versions: Mapped[list[WorkflowVersion]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="WorkflowVersion.version.desc()",
    )

    __table_args__ = (
        CheckConstraint("length(name) > 0", name="ck_workflows_name"),
        UniqueConstraint(
            "organization_id", "name", name="uq_workflows_org_name"
        ),
        Index(
            "ix_workflows_org",
            "organization_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Workflow {self.name}>"


class WorkflowVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One frozen (or in-progress) definition of a workflow."""

    __tablename__ = "workflow_versions"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workflow_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )

    #: Denormalised out of `definition` so event matching is an index lookup.
    #: Every CRM mutation emits an event, so this query runs constantly and a
    #: JSONB scan across the tenant's workflows would be on the write path of
    #: every record edit.
    trigger_type: Mapped[str] = mapped_column(String(60), nullable=False)

    #: The node graph. Validated against the registries before publish, never
    #: interpreted while it is a draft.
    definition: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    workflow: Mapped[Workflow] = relationship(back_populates="versions")

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_workflow_versions_status",
        ),
        CheckConstraint("version > 0", name="ck_workflow_versions_version"),
        UniqueConstraint(
            "workflow_id", "version", name="uq_workflow_versions_number"
        ),
        # The dispatcher's query: enabled workflows in this tenant whose
        # published version listens for this event.
        Index(
            "ix_workflow_versions_trigger",
            "organization_id",
            "trigger_type",
            postgresql_where=text("status = 'published'"),
        ),
        Index("ix_workflow_versions_workflow", "workflow_id", "version"),
    )

    def __repr__(self) -> str:
        return f"<WorkflowVersion {self.workflow_id} v{self.version} {self.status}>"


class WorkflowEvent(Base, UUIDPrimaryKeyMixin):
    """The transactional outbox.

    Written by CRM services in the same transaction as the change it describes,
    so a rolled-back edit leaves no event behind. A dispatcher job — enqueued
    optimistically and re-found by a sweep if that enqueue is lost — turns it
    into workflow runs.

    Distinct from `audit_logs` on purpose, at the same moments. The audit log
    answers *what happened, for the record*; this answers *what should react*.
    Conflating them would put the automation engine's retry and replay
    requirements onto a table whose value comes from being append-only and
    never re-processed.
    """

    __tablename__ = "workflow_events"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    #: A trigger registry key, e.g. `lead.created`, `deal.stage_changed`.
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)

    entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )
    #: Who caused it. NULL for machine-initiated changes, which is also how a
    #: workflow's own writes are distinguished from a person's — see the
    #: recursion guard in `app/automation/events.py`.
    actor_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: A snapshot of what changed: the record's readable fields plus, for
    #: updates, the before/after diff. Snapshotted rather than re-read at
    #: dispatch time so a condition sees the state that triggered it, not the
    #: state after three more edits landed.
    payload: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    #: Set once the dispatcher has matched it against workflows. The sweep
    #: looks for NULL, which is what makes a lost enqueue survivable.
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # The sweep's query: undispatched, oldest first. Partial, and normally
        # near-empty, because the fast path dispatches within milliseconds.
        Index(
            "ix_workflow_events_pending",
            "occurred_at",
            postgresql_where=text("dispatched_at IS NULL"),
        ),
        Index("ix_workflow_events_entity", "organization_id", "entity_type", "entity_id"),
    )

    def __repr__(self) -> str:
        return f"<WorkflowEvent {self.event_type}>"


class WorkflowRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One execution of one version, against one record."""

    __tablename__ = "workflow_runs"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workflow_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Pinned, so editing the workflow cannot change what a running execution
    #: does halfway through.
    version_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("workflow_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("workflow_events.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )

    entity_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )

    #: Variables carried between nodes: the trigger payload plus whatever
    #: actions produced. Bounded by the executor, because an unbounded context
    #: is how a long workflow turns into a multi-megabyte row.
    context: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )

    #: Where to resume. NULL when the run has not started or has finished.
    current_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: When a `waiting` run becomes runnable again.
    resume_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: User-facing failure text. The stack trace goes to the log.
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: How many times this run has been picked up. Bounds a run that keeps
    #: failing on a transient fault from retrying forever.
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    steps: Mapped[list[WorkflowRunStep]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="WorkflowRunStep.sequence",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'waiting', 'succeeded', "
            "'failed', 'cancelled')",
            name="ck_workflow_runs_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_workflow_runs_attempts"),
        # The run list: a workflow's history, newest first.
        Index(
            "ix_workflow_runs_workflow",
            "organization_id",
            "workflow_id",
            "created_at",
        ),
        # The resume sweep: parked runs whose timer has elapsed. Partial and
        # normally small.
        Index(
            "ix_workflow_runs_resumable",
            "resume_at",
            postgresql_where=text("status = 'waiting'"),
        ),
        # A record's automation history, for the detail page.
        Index(
            "ix_workflow_runs_entity",
            "organization_id",
            "entity_type",
            "entity_id",
        ),
    )

    def __repr__(self) -> str:
        return f"<WorkflowRun {self.workflow_id} {self.status}>"


class WorkflowRunStep(Base, UUIDPrimaryKeyMixin):
    """What one node did, for the execution log.

    The log is the whole point of the feature being operable: "why did this
    client get that email" has to have an answer, and it has to survive the
    workflow being edited afterwards — which is why the node's label is copied
    in rather than looked up.
    """

    __tablename__ = "workflow_run_steps"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Position in the run, so the log reads in execution order even when two
    #: steps share a timestamp.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(30), nullable=False)
    #: Copied from the definition at execution time. A later edit must not
    #: rewrite history.
    node_label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: What the node produced or decided — a condition's boolean, an action's
    #: created id. Redacted like every other JSONB payload.
    output: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, default=dict, server_default="{}"
    )
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    run: Mapped[WorkflowRun] = relationship(back_populates="steps")

    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'skipped')",
            name="ck_workflow_run_steps_status",
        ),
        UniqueConstraint("run_id", "sequence", name="uq_workflow_run_steps_sequence"),
        Index("ix_workflow_run_steps_run", "run_id", "sequence"),
    )

    def __repr__(self) -> str:
        return f"<WorkflowRunStep {self.node_id} {self.status}>"
