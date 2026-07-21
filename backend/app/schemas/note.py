"""Note contracts.

`content_format` is accepted on write but defaults to `markdown`, the safe path
the frontend renders itself rather than trusting as HTML. The body is required
and bounded — a note is a document, but not an unbounded one.

`entity_type`/`entity_id` are required on create (a note is always about a
record) and absent from update (moving a note to another record rewrites what it
annotates, and is better expressed as delete-and-rewrite).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

NoteContentFormat = Literal["markdown", "html", "plain"]
EntityType = Literal["lead", "client", "property", "deal", "task"]


class NoteAuthor(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    initials: str
    avatar_hue: int


class NoteCreate(BaseModel):
    entity_type: EntityType
    entity_id: UUID
    title: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=50_000)
    content_format: NoteContentFormat = "markdown"
    is_pinned: bool = False


class NoteUpdate(BaseModel):
    """Partial update. The entity link is deliberately not editable."""

    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=50_000)
    content_format: NoteContentFormat | None = None
    is_pinned: bool | None = None


class NoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_type: str
    entity_id: UUID
    title: str | None
    body: str
    content_format: str
    is_pinned: bool
    author: NoteAuthor | None
    #: True for notes the caller wrote — the UI shows edit controls only then.
    is_own: bool = False
    created_at: datetime
    updated_at: datetime


class NoteFilters(BaseModel):
    """Explicit filter parameters. Deliberately not a generic query DSL."""

    search: str | None = Field(default=None, max_length=200)
    entity_type: EntityType | None = None
    entity_id: UUID | None = None
    author_id: UUID | None = None
    pinned: bool | None = None

    @model_validator(mode="after")
    def _entity_id_needs_a_type(self) -> NoteFilters:
        if self.entity_id is not None and self.entity_type is None:
            raise ValueError("entity_type is required when filtering by entity_id")
        return self

    def entity_link(self) -> tuple[str, UUID] | None:
        if self.entity_id is not None and self.entity_type is not None:
            return self.entity_type, self.entity_id
        return None
