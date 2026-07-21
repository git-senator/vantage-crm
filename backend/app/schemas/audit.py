"""Audit log contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    action: str
    actor_id: UUID | None
    actor_email: str | None
    entity_type: str | None
    entity_id: UUID | None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")
    ip_address: str | None
    request_id: str | None
    created_at: datetime

    @field_validator("ip_address", mode="before")
    @classmethod
    def _stringify_inet(cls, value: object) -> str | None:
        """asyncpg returns INET as an ipaddress object, not a string."""
        return None if value is None else str(value)
