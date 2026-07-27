"""API key contracts.

The secret crosses the wire exactly once — in the response to creation and
rotation (`ApiKeyCreated`). Every other shape carries only the public identifier
(`prefix` + `last_four`), never anything from which the key could be reconstructed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ScopeName = Literal["own", "team", "all"]
Environment = Literal["live", "sandbox"]


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    #: `{permission_key: scope}` — each bounded at creation to a subset of the
    #: creator's own grants.
    scopes: dict[str, ScopeName] = Field(min_length=1)
    #: Optional lifetime in days. Omitted is a non-expiring key (subject to the
    #: workspace's maximum).
    expires_in_days: int | None = Field(default=None, ge=1)
    #: `live` (the default) or `sandbox`. A sandbox key is a test credential —
    #: it skips the plan gate and API-key quota (Phase 7.6).
    environment: Environment = "live"


class ApiKeyRead(BaseModel):
    id: UUID
    name: str
    #: Public identifier, safe to display (e.g. `vk_Ab12Cd34`).
    prefix: str
    last_four: str
    environment: str
    scopes: dict[str, str]
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    is_active: bool


class ApiKeyCreated(ApiKeyRead):
    """Returned only at creation and rotation. Carries the one-time secret."""

    #: The raw key. Shown once and never recoverable — store it now.
    secret: str


def to_read(key: object) -> ApiKeyRead:
    from app.models.api_key import ApiKey

    assert isinstance(key, ApiKey)
    return ApiKeyRead(
        id=key.id,
        name=key.name,
        prefix=key.prefix,
        last_four=key.last_four,
        environment=key.environment,
        scopes=dict(key.scopes),
        expires_at=key.expires_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
        created_at=key.created_at,
        is_active=key.is_active,
    )


def to_created(key: object, secret: str) -> ApiKeyCreated:
    base = to_read(key)
    return ApiKeyCreated(**base.model_dump(), secret=secret)
