"""Access-request and invitation contracts.

The public submit and the accept-invite flow are both unauthenticated, so these
schemas are careful about what they echo back: a submit confirmation reveals
nothing about who already has an account, and the invitation lookup returns only
what the accept page needs to greet the person.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.config import get_settings

#: Roles the request queue can grant. `owner` is deliberately absent — a
#: workspace has one owner, set at bootstrap, never handed out from a form.
ASSIGNABLE_ROLES = ("admin", "manager", "agent")


class AccessRequestCreate(BaseModel):
    """The public "request access" form."""

    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    requested_role: str | None = Field(default=None, max_length=60)
    message: str | None = Field(default=None, max_length=1000)

    @field_validator("full_name")
    @classmethod
    def _trim(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Name is required")
        return trimmed


class AccessRequestRead(BaseModel):
    """A row in the admin review queue."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    email: str
    requested_role: str | None
    message: str | None
    status: str
    created_at: datetime
    reviewed_at: datetime | None = None


class AccessRequestApprove(BaseModel):
    role_key: str = Field(description="One of: admin, manager, agent")

    @field_validator("role_key")
    @classmethod
    def _known_role(cls, value: str) -> str:
        if value not in ASSIGNABLE_ROLES:
            raise ValueError(f"role_key must be one of {', '.join(ASSIGNABLE_ROLES)}")
        return value


class InvitationInfo(BaseModel):
    """What the accept-invite page shows before a password is set."""

    email: str
    full_name: str
    organization_name: str


class InvitationAccept(BaseModel):
    password: str = Field(max_length=256)

    @field_validator("password")
    @classmethod
    def _meets_policy(cls, value: str) -> str:
        # Same policy as PasswordChangeRequest — length over composition rules.
        minimum = get_settings().PASSWORD_MIN_LENGTH
        if len(value) < minimum:
            raise ValueError(f"Password must be at least {minimum} characters")
        if value.strip() != value:
            raise ValueError("Password must not begin or end with whitespace")
        return value
