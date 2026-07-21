"""Task contracts.

`completed_at` is absent from every write model. Completing a task is
`POST /tasks/{id}/complete`, which sets the timestamp, emits an activity on the
linked record and audits — the same reasoning that keeps `stage_id` off
`DealUpdate`. A `completed_at` a client could set independently of `status`
would break `ck_tasks_completed_at` and make "how long did this take"
unanswerable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

TaskStatus = Literal["todo", "in_progress", "blocked", "done"]
TaskPriority = Literal["low", "medium", "high", "urgent"]
EntityType = Literal["lead", "client", "property", "deal"]


class TaskAssignee(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class TaskBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    priority: TaskPriority = "medium"
    due_at: datetime | None = None
    entity_type: EntityType | None = None
    entity_id: UUID | None = None

    @model_validator(mode="after")
    def _entity_link_is_whole(self) -> TaskBase:
        """Mirrors ck_tasks_entity_pair: half a reference points nowhere."""
        if (self.entity_type is None) != (self.entity_id is None):
            raise ValueError(
                "entity_type and entity_id must be provided together."
            )
        return self


class TaskCreate(TaskBase):
    """New task. Assignee defaults to the creator when omitted.

    `status` is absent: a task starts as `todo`. Creating one already done is
    not a thing anyone means to do, and it would need a completed_at the
    creator never supplied.
    """

    assignee_id: UUID | None = None


class TaskUpdate(BaseModel):
    """Partial update. `status` is here but `done` is refused — see the service.

    Moving to `done` through a PATCH would skip the completion timestamp, the
    activity and the audit entry. Use the complete endpoint.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_at: datetime | None = None
    entity_type: EntityType | None = None
    entity_id: UUID | None = None
    assignee_id: UUID | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str | None
    status: str
    priority: str
    due_at: datetime | None
    completed_at: datetime | None
    #: Derived from due_at and status. Never stored — a stored flag is correct
    #: until the clock moves.
    is_overdue: bool
    entity_type: str | None
    entity_id: UUID | None
    assignee: TaskAssignee | None
    created_at: datetime
    updated_at: datetime


class TaskFilters(BaseModel):
    """Explicit filter parameters. Deliberately not a generic query DSL."""

    search: str | None = Field(default=None, max_length=200)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    assignee_id: UUID | None = None
    entity_type: EntityType | None = None
    entity_id: UUID | None = None
    #: Convenience for the dashboard: open and past due.
    overdue: bool | None = None
    due_before: datetime | None = None
    due_after: datetime | None = None

    @model_validator(mode="after")
    def _entity_id_needs_a_type(self) -> TaskFilters:
        if self.entity_id is not None and self.entity_type is None:
            raise ValueError("entity_type is required when filtering by entity_id")
        return self


class TaskAssign(BaseModel):
    assignee_id: UUID


class TaskComplete(BaseModel):
    """Completing a task may carry a closing note onto the entity timeline."""

    note: str | None = Field(default=None, max_length=2_000)
