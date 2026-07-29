"""Marketplace developer platform contracts (Phase 9.5).

A credential's raw secret is returned exactly once, at creation
(:class:`CredentialCreateResult`); every other projection reports only the prefix
and last four characters, never the hash and never the secret.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ------------------------------------------------------- developer orgs


class DeveloperOrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    contact_email: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class DeveloperOrganizationRead(BaseModel):
    id: UUID
    name: str
    contact_email: str | None
    description: str | None
    status: str
    created_at: datetime


# ------------------------------------------------------- applications


class ApplicationRegister(BaseModel):
    developer_org_id: UUID
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=3, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)
    plugin_id: UUID | None = None


class ApplicationRead(BaseModel):
    id: UUID
    developer_org_id: UUID
    plugin_id: UUID | None
    name: str
    slug: str
    metadata: dict[str, Any]
    lifecycle_status: str
    submitted_at: datetime | None
    approved_at: datetime | None
    published_at: datetime | None
    created_at: datetime


# ------------------------------------------------------- reviews


class ReviewDecision(BaseModel):
    version: str = Field(default="1.0.0", min_length=1, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)


class ApplicationReviewRead(BaseModel):
    id: UUID
    application_id: UUID
    version: str
    status: str
    notes: str | None
    reviewer_id: UUID | None
    created_at: datetime


# ------------------------------------------------------- credentials


class CredentialCreate(BaseModel):
    developer_org_id: UUID
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=list)


class CredentialRead(BaseModel):
    id: UUID
    developer_org_id: UUID
    name: str
    prefix: str
    last_four: str
    scopes: list[str]
    is_active: bool
    revoked_at: datetime | None
    created_at: datetime


class CredentialCreateResult(BaseModel):
    credential: CredentialRead
    #: The raw secret — shown exactly once, at creation, and never retrievable.
    secret: str


__all__ = [
    "ApplicationRead",
    "ApplicationRegister",
    "ApplicationReviewRead",
    "CredentialCreate",
    "CredentialCreateResult",
    "CredentialRead",
    "DeveloperOrganizationCreate",
    "DeveloperOrganizationRead",
    "ReviewDecision",
]
