"""Organization contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    plan: str
    settings: dict[str, Any]
    created_at: datetime


class OrganizationUpdate(BaseModel):
    """Partial update.

    `slug` and `plan` are deliberately absent: slug changes break links and
    future subdomain routing, and plan is a billing concern, not a settings
    edit. Both get dedicated, audited operations when they are needed.
    """

    name: str | None = Field(default=None, min_length=2, max_length=200)
    settings: dict[str, Any] | None = None


class OrganizationMember(BaseModel):
    """A user as seen from the organization's member list."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    initials: str
    job_title: str | None
    avatar_hue: int
    status: str
    last_login_at: datetime | None
    created_at: datetime
