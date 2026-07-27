"""Enterprise governance contracts.

Update schemas are partial: every field is optional and the service applies only
what was set (`exclude_unset`), so a PATCH that touches one field leaves the rest
alone. Write-only secrets (the SSO client secret and certificate) appear on the
update schema but never on a read projection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.enterprise.policies import valid_cidr

_HEX_COLOR = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"


# ------------------------------------------------------------ security


class SecurityPolicyRead(BaseModel):
    password_min_length: int
    password_require_upper: bool
    password_require_lower: bool
    password_require_number: bool
    password_require_symbol: bool
    password_expiry_days: int | None
    mfa_required: bool
    mfa_grace_days: int
    session_idle_timeout_minutes: int | None
    session_absolute_hours: int | None
    max_concurrent_sessions: int | None
    sessions_valid_after: datetime | None
    ip_allowlist: list[str]
    ip_enforcement: bool


class SecurityPolicyUpdate(BaseModel):
    password_min_length: int | None = Field(default=None, ge=8, le=128)
    password_require_upper: bool | None = None
    password_require_lower: bool | None = None
    password_require_number: bool | None = None
    password_require_symbol: bool | None = None
    password_expiry_days: int | None = Field(default=None, ge=1, le=3650)
    mfa_required: bool | None = None
    mfa_grace_days: int | None = Field(default=None, ge=0, le=365)
    session_idle_timeout_minutes: int | None = Field(default=None, ge=1, le=44640)
    session_absolute_hours: int | None = Field(default=None, ge=1, le=8760)
    max_concurrent_sessions: int | None = Field(default=None, ge=1, le=100)
    ip_allowlist: list[str] | None = None
    ip_enforcement: bool | None = None

    @field_validator("ip_allowlist")
    @classmethod
    def _cidrs_are_valid(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        for entry in value:
            if not valid_cidr(entry):
                raise ValueError(f"'{entry}' is not a valid IP address or CIDR range.")
        return value


class PasswordCheck(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class PasswordCheckResult(BaseModel):
    ok: bool
    violations: list[str]


class SessionRevokeResult(BaseModel):
    sessions_valid_after: datetime


# ------------------------------------------------------------- branding


class BrandingRead(BaseModel):
    logo_url: str | None
    icon_url: str | None
    primary_color: str | None
    accent_color: str | None
    login_heading: str | None
    login_subheading: str | None
    email_from_name: str | None
    email_footer: str | None
    support_url: str | None
    custom_domain: str | None
    is_published: bool


class BrandingUpdate(BaseModel):
    logo_url: str | None = Field(default=None, max_length=2048)
    icon_url: str | None = Field(default=None, max_length=2048)
    primary_color: str | None = Field(default=None, pattern=_HEX_COLOR)
    accent_color: str | None = Field(default=None, pattern=_HEX_COLOR)
    login_heading: str | None = Field(default=None, max_length=200)
    login_subheading: str | None = Field(default=None, max_length=500)
    email_from_name: str | None = Field(default=None, max_length=120)
    email_footer: str | None = Field(default=None, max_length=1000)
    support_url: str | None = Field(default=None, max_length=2048)
    custom_domain: str | None = Field(default=None, max_length=255)
    is_published: bool | None = None


# ----------------------------------------------------------- compliance


class CompliancePolicyRead(BaseModel):
    retention_days: dict[str, int]
    legal_hold: bool
    legal_hold_reason: str | None
    dpo_email: str | None


class CompliancePolicyUpdate(BaseModel):
    retention_days: dict[str, int] | None = None
    dpo_email: str | None = Field(default=None, max_length=255)

    @field_validator("retention_days")
    @classmethod
    def _windows_are_positive(
        cls, value: dict[str, int] | None
    ) -> dict[str, int] | None:
        if value is None:
            return None
        for entity, days in value.items():
            if days < 1:
                raise ValueError(f"Retention for '{entity}' must be at least 1 day.")
        return value


class LegalHoldUpdate(BaseModel):
    enabled: bool
    reason: str | None = Field(default=None, max_length=500)


class DataRequestCreate(BaseModel):
    kind: Literal["export", "deletion"]
    subject_email: str = Field(min_length=3, max_length=255)


class DataRequestRead(BaseModel):
    id: UUID
    kind: str
    subject_email: str
    subject_user_id: UUID | None
    status: str
    result: dict[str, object]
    error: str | None
    processed_at: datetime | None
    created_at: datetime


# ------------------------------------------------------------------ sso


class SsoConnectionRead(BaseModel):
    protocol: str
    is_enabled: bool
    display_name: str | None
    oidc_issuer: str | None
    oidc_client_id: str | None
    #: True when a client secret is stored — never the secret itself.
    oidc_client_secret_set: bool
    saml_entity_id: str | None
    saml_sso_url: str | None
    saml_x509_cert_set: bool
    jit_enabled: bool
    default_role_key: str
    allowed_domains: list[str]
    attribute_mapping: dict[str, str]
    status: str


class SsoConnectionUpdate(BaseModel):
    protocol: Literal["saml", "oidc"] | None = None
    is_enabled: bool | None = None
    display_name: str | None = Field(default=None, max_length=120)
    oidc_issuer: str | None = Field(default=None, max_length=2048)
    oidc_client_id: str | None = Field(default=None, max_length=255)
    #: Write-only. Sealed at rest; never echoed back.
    oidc_client_secret: str | None = Field(default=None, max_length=2048)
    saml_entity_id: str | None = Field(default=None, max_length=2048)
    saml_sso_url: str | None = Field(default=None, max_length=2048)
    #: Write-only. Sealed at rest; never echoed back.
    saml_x509_cert: str | None = Field(default=None, max_length=8192)
    jit_enabled: bool | None = None
    default_role_key: str | None = Field(default=None, max_length=50)
    allowed_domains: list[str] | None = None
    attribute_mapping: dict[str, str] | None = None


# -------------------------------------------------------------- features


class FeatureFlagUpdate(BaseModel):
    enabled: bool
    note: str | None = Field(default=None, max_length=500)


class FeatureView(BaseModel):
    #: What the billing plan grants.
    plan_features: dict[str, bool]
    #: Per-tenant overrides layered on top.
    overrides: dict[str, bool]
    #: The resolved answer the app gates on.
    effective: dict[str, bool]


# ---------------------------------------------------------- tenant health


class TenantHealth(BaseModel):
    organization_id: UUID
    plan: str | None
    users_active: int
    sso_enabled: bool
    mfa_required: bool
    ip_enforcement: bool
    legal_hold: bool
    open_data_requests: int
    branding_published: bool
    #: A coarse posture summary: `healthy` | `attention`.
    posture: str


__all__ = [
    "BrandingRead",
    "BrandingUpdate",
    "CompliancePolicyRead",
    "CompliancePolicyUpdate",
    "DataRequestCreate",
    "DataRequestRead",
    "FeatureFlagUpdate",
    "FeatureView",
    "LegalHoldUpdate",
    "PasswordCheck",
    "PasswordCheckResult",
    "SecurityPolicyRead",
    "SecurityPolicyUpdate",
    "SessionRevokeResult",
    "SsoConnectionRead",
    "SsoConnectionUpdate",
    "TenantHealth",
]
