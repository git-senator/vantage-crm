"""Report contracts.

The specification crosses the wire as a **typed object**, not free text. Pydantic
rejects an unknown operator here and the registry rejects an unknown field name
in `resolve_spec` — two layers, because this is the surface where a report
builder could become a SQL console.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ExportFormat = Literal["csv", "xlsx", "pdf"]
Schedule = Literal["none", "daily", "weekly", "monthly"]
AggregateFunction = Literal["count", "sum", "avg", "min", "max"]
FilterOperator = Literal[
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "starts_with",
    "in",
    "not_in",
    "is_null",
    "is_not_null",
    "between",
]


class FilterSpec(BaseModel):
    field: str = Field(min_length=1, max_length=60)
    operator: FilterOperator = "eq"
    #: Untyped on purpose — the value's type depends on the field's, and
    #: `query._coerce` converts it once the field is known. Validating it here
    #: would need the registry, which is where it happens.
    value: Any = None


class AggregateSpec(BaseModel):
    function: AggregateFunction = "count"
    field: str | None = None
    key: str | None = None
    label: str | None = None


class ReportSpecIn(BaseModel):
    dataset: str = Field(min_length=1, max_length=40)
    columns: list[str] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    aggregates: list[AggregateSpec] = Field(default_factory=list)
    sort: str | None = None
    sort_desc: bool = True
    limit: int | None = Field(default=None, ge=1, le=50_000)
    #: Opt-in, per report. A column visible in the UI is not on its own an
    #: argument for putting it in a file that leaves the building.
    include_sensitive: bool = False


class FieldRead(BaseModel):
    key: str
    label: str
    type: str
    groupable: bool
    aggregatable: bool
    sensitive: bool


class DatasetRead(BaseModel):
    key: str
    label: str
    description: str
    fields: list[FieldRead]


class ReportPreview(BaseModel):
    headers: list[str]
    rows: list[list[Any]]
    row_count: int
    #: What the report would return without the cap. `truncated` is derived from
    #: it so a client cannot mistake a page for the whole answer.
    total_rows: int
    truncated: bool


class ReportDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    dataset: str
    definition: dict[str, Any]
    is_shared: bool
    schedule: Schedule
    schedule_format: ExportFormat
    owner_id: UUID | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReportDefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    definition: ReportSpecIn
    is_shared: bool = False
    schedule: Schedule = "none"
    schedule_format: ExportFormat = "xlsx"
    recipients: list[UUID] = Field(default_factory=list)


class ReportDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    definition: ReportSpecIn | None = None
    is_shared: bool | None = None
    schedule: Schedule | None = None
    schedule_format: ExportFormat | None = None
    recipients: list[UUID] | None = None


class ReportRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    definition_id: UUID | None
    name: str
    dataset: str
    format: ExportFormat
    #: `partial` is a success that hit the row cap — a distinct state, because a
    #: truncated export that reports success is the bug the status exists for.
    status: Literal["queued", "running", "succeeded", "partial", "failed"]
    is_scheduled: bool
    row_count: int
    total_rows: int
    size_bytes: int | None
    error: str | None
    requested_by: UUID | None
    started_at: datetime
    completed_at: datetime | None


class ExportRequest(BaseModel):
    """Either a saved report or an ad-hoc specification, never both."""

    definition_id: UUID | None = None
    definition: ReportSpecIn | None = None
    format: ExportFormat = "xlsx"


class DownloadResponse(BaseModel):
    url: str
    expires_in: int
