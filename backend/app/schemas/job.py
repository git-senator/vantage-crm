"""Background-job contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobFailureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID | None
    job_name: str
    job_key: str
    job_args: dict[str, Any] = Field(default_factory=dict)
    attempts: int
    error_class: str
    error_message: str
    first_failed_at: datetime
    last_failed_at: datetime
    resolved_at: datetime | None


class QueueHealth(BaseModel):
    """What an operator wants at a glance before opening the list."""

    #: False when Redis is unreachable — jobs are not being processed at all,
    #: which no count of dead letters would reveal on its own.
    queue_reachable: bool
    queued_jobs: int | None
    open_failures: int
