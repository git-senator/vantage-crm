"""Authentication request and response contracts.

Response models are explicit and never expose an ORM object. Declaring every
field that crosses the boundary means adding a column (say, `mfa_secret`)
cannot silently start leaking it (OWASP A03/A05).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.config import get_settings


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)

    # Deliberately no min_length matching the password policy: rejecting a
    # short password at login with a distinct error would confirm the policy
    # to an attacker and slow nothing down. Length is enforced on registration.


class RefreshRequest(BaseModel):
    """Body is optional — the refresh token normally arrives as a cookie."""

    refresh_token: str | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(max_length=256)

    @field_validator("new_password")
    @classmethod
    def _meets_policy(cls, value: str) -> str:
        minimum = get_settings().PASSWORD_MIN_LENGTH
        if len(value) < minimum:
            raise ValueError(f"Password must be at least {minimum} characters")
        # Length over composition rules, per NIST SP 800-63B: composition rules
        # push users toward predictable substitutions without adding entropy.
        if value.strip() != value:
            raise ValueError("Password must not begin or end with whitespace")
        return value


class ProfileUpdate(BaseModel):
    """The fields a user may edit on their own profile.

    Deliberately small: identity (email), role and status are not self-editable.
    A missing field is left unchanged; an explicit null clears an optional one.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    job_title: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    avatar_hue: int | None = Field(default=None, ge=0, le=359)


class OrganizationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str


class UserProfile(BaseModel):
    """The authenticated user, as returned by /auth/me and /auth/login.

    Shaped to match the frontend's existing `SessionUser` type so the sidebar
    and avatar components need no change.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    initials: str
    job_title: str | None = None
    phone: str | None = None
    avatar_hue: int
    status: str
    mfa_enabled: bool
    last_login_at: datetime | None = None
    organization: OrganizationSummary
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class SessionResponse(BaseModel):
    """Returned by login and refresh.

    Contains no tokens: both are set as httpOnly cookies and are never readable
    by JavaScript. `expires_at` lets the client schedule a pre-emptive refresh.
    """

    user: UserProfile
    expires_at: datetime
    csrf_token: str


class MessageResponse(BaseModel):
    message: str
