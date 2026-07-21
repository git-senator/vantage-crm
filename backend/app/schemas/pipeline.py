"""Pipeline contracts.

A pipeline and its stages are workspace *configuration*, not CRM data, which is
why writes are gated on `settings.manage` rather than `deals.manage`: choosing
the funnel every agent works in is an admin decision, and an agent who can edit
deals must not be able to delete the stage those deals sit in.

Reads are gated on `deals.view`, because the board cannot render without them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

StageKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=40,
        # Machine name: analytics group by this, so it must be stable and
        # predictable. The display label lives in `name` and is free text.
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]


class PipelineStageBase(BaseModel):
    key: StageKey
    name: str = Field(min_length=1, max_length=80)
    position: int = Field(default=0, ge=0, le=999)
    default_probability: int = Field(default=0, ge=0, le=100)
    is_won: bool = False
    is_lost: bool = False

    @model_validator(mode="after")
    def _outcome_is_coherent(self) -> PipelineStageBase:
        """Mirrors ck_pipeline_stages_outcome."""
        if self.is_won and self.is_lost:
            raise ValueError("A stage cannot be both won and lost.")
        return self


class PipelineStageCreate(PipelineStageBase):
    pass


class PipelineStageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    position: int | None = Field(default=None, ge=0, le=999)
    default_probability: int | None = Field(default=None, ge=0, le=100)
    is_won: bool | None = None
    is_lost: bool | None = None


class PipelineStageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    name: str
    position: int
    default_probability: int
    is_won: bool
    is_lost: bool
    is_terminal: bool


class PipelineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    is_default: bool = False
    #: At least one stage — a pipeline with no stages has nowhere to put a deal.
    stages: list[PipelineStageCreate] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _stage_keys_are_unique(self) -> PipelineCreate:
        keys = [stage.key for stage in self.stages]
        if len(keys) != len(set(keys)):
            raise ValueError("Stage keys must be unique within a pipeline.")
        # More than one winning stage makes "did we win?" ambiguous, and every
        # conversion metric downstream depends on that answer.
        if sum(stage.is_won for stage in self.stages) > 1:
            raise ValueError("A pipeline can have at most one winning stage.")
        if sum(stage.is_lost for stage in self.stages) > 1:
            raise ValueError("A pipeline can have at most one losing stage.")
        return self


class PipelineUpdate(BaseModel):
    """Metadata only. Stages are managed through their own endpoints, because
    reordering and retiring a stage have consequences for live deals that a
    blanket PUT would hide."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    is_default: bool | None = None


class PipelineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    is_default: bool
    stages: list[PipelineStageRead]
    created_at: datetime
    updated_at: datetime
