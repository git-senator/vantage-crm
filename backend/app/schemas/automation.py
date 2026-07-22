"""Automation contracts.

The definition travels as an opaque `dict` rather than a typed model, and that
is deliberate. A Pydantic model of the node graph would reject a half-built
draft at the API boundary — which is exactly what a draft is — and the builder
saves on every change. Structure is checked by `app/automation/definition.py`,
which returns *every* problem rather than the first, and only refuses at publish.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkflowAuthor(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    trigger_type: str = Field(min_length=1, max_length=60)
    #: Optional starting definition. Absent means an empty draft with just the
    #: trigger, which is what the builder opens with.
    definition: dict[str, Any] | None = None


class WorkflowUpdate(BaseModel):
    """Name and description only. The definition has its own endpoint, because
    saving a graph and renaming a workflow are different operations with
    different failure modes."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)


class WorkflowVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    status: str
    trigger_type: str
    definition: dict[str, Any]
    published_at: datetime | None
    created_at: datetime


class WorkflowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    is_enabled: bool
    published_version_id: UUID | None
    author: WorkflowAuthor | None
    #: The live version's trigger, so a list can show what fires each workflow
    #: without the client fetching every version.
    trigger_type: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowDetail(BaseModel):
    workflow: WorkflowRead
    #: What the builder opens: the draft if there is one, otherwise the
    #: published version.
    draft: WorkflowVersionRead | None
    published: WorkflowVersionRead | None
    versions: list[WorkflowVersionRead] = Field(default_factory=list)


class DefinitionSave(BaseModel):
    definition: dict[str, Any]


class ValidationResult(BaseModel):
    """Every problem at once, so the builder can mark up the whole canvas."""

    valid: bool
    errors: list[str] = Field(default_factory=list)


class EnabledUpdate(BaseModel):
    is_enabled: bool


class WorkflowRunStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sequence: int
    node_id: str
    node_type: str
    node_label: str | None
    status: str
    output: dict[str, Any]
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class WorkflowRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workflow_id: UUID
    version_id: UUID
    status: str
    entity_type: str | None
    entity_id: UUID | None
    current_node_id: str | None
    resume_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    attempts: int
    created_at: datetime


class WorkflowRunDetail(BaseModel):
    run: WorkflowRunRead
    steps: list[WorkflowRunStepRead] = Field(default_factory=list)


# ------------------------------------------------------------- registries


class FieldOptionRead(BaseModel):
    value: str
    label: str


class FieldSpecRead(BaseModel):
    key: str
    label: str
    kind: str
    required: bool
    help_text: str | None
    options: list[FieldOptionRead] = Field(default_factory=list)
    default: Any = None


class RegistryEntryRead(BaseModel):
    key: str
    label: str
    description: str
    category: str
    fields: list[FieldSpecRead] = Field(default_factory=list)
    #: Triggers only.
    entity_type: str | None = None
    #: Actions only.
    entity_types: list[str] = Field(default_factory=list)
    external: bool = False
    #: Condition operators only.
    takes_value: bool | None = None
    value_kind: str | None = None


class RegistriesRead(BaseModel):
    """Everything the builder needs to draw its palette.

    Served from the same registries the executor uses, so the two cannot drift
    — which is the whole reason the palette is not a hand-written list in the
    frontend.
    """

    triggers: list[RegistryEntryRead]
    actions: list[RegistryEntryRead]
    operators: list[RegistryEntryRead]
