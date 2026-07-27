"""Enterprise governance models (Phase 8.0).

Six tenant-scoped, RLS-FORCEd tables, each a single administrative concern:

  * ``SecurityPolicy`` — one per org: password rules, MFA enforcement, session
    limits, and the IP allowlist.
  * ``OrganizationBranding`` — one per org: the white-label surface (logo,
    colours, login and email copy, custom domain).
  * ``CompliancePolicy`` — one per org: data-retention windows, legal hold, DPO
    contact.
  * ``DataRequest`` — a GDPR subject-access (export) or erasure (deletion)
    request, processed by a worker.
  * ``SsoConnection`` — one per org: SAML/OIDC configuration and JIT settings.
    The client secret and signing certificate are encrypted at rest.
  * ``FeatureFlag`` — a per-tenant feature override on top of the billing plan.

The "one per org" tables carry a UNIQUE(organization_id) so the service can
upsert a singleton without racing a second row into existence.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


def _org_fk() -> Mapped[UUID]:
    return mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )


def _actor_fk() -> Mapped[UUID | None]:
    return mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SecurityPolicy(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "security_policies"

    organization_id: Mapped[UUID] = _org_fk()

    password_min_length: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="12"
    )
    password_require_upper: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    password_require_lower: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    password_require_number: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    password_require_symbol: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: Force a change after this many days. NULL never expires.
    password_expiry_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    mfa_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: Days a user may sign in after MFA becomes required before it is enforced.
    mfa_grace_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="7"
    )

    session_idle_timeout_minutes: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    session_absolute_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_concurrent_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Sessions issued at or before this instant are revoked — the wholesale
    #: "sign everyone out" control.
    sessions_valid_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    ip_allowlist: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    ip_enforcement: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_security_policies_org"),
    )


class OrganizationBranding(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "organization_branding"

    organization_id: Mapped[UUID] = _org_fk()

    logo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    icon_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    #: Hex colours, validated at the schema layer.
    primary_color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    accent_color: Mapped[str | None] = mapped_column(String(9), nullable=True)

    login_heading: Mapped[str | None] = mapped_column(String(200), nullable=True)
    login_subheading: Mapped[str | None] = mapped_column(String(500), nullable=True)

    email_from_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email_footer: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    support_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    #: A vanity domain the login page is served from. Verification is out of band.
    custom_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_organization_branding_org"),
    )


class CompliancePolicy(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "compliance_policies"

    organization_id: Mapped[UUID] = _org_fk()

    #: Retention window in days, per record class: {"audit_logs": 365, ...}.
    #: A class that is absent is kept forever.
    retention_days: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )
    #: When set, retention deletion is suspended workspace-wide — the legal-hold
    #: override that must win over any retention window.
    legal_hold: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    legal_hold_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dpo_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_compliance_policies_org"),
    )


class DataRequest(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "data_requests"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()

    #: export (GDPR subject access) | deletion (GDPR erasure).
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )

    #: pending | processing | completed | failed.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )
    #: A summary of what the run produced (counts, redactions) — never the
    #: subject's data itself, which is delivered out of band.
    result: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        Index("ix_data_requests_org", "organization_id", "created_at"),
        CheckConstraint(
            "kind IN ('export', 'deletion')", name="ck_data_requests_kind"
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_data_requests_status",
        ),
    )


class SsoConnection(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "sso_connections"

    organization_id: Mapped[UUID] = _org_fk()
    created_by: Mapped[UUID | None] = _actor_fk()

    #: saml | oidc.
    protocol: Mapped[str] = mapped_column(String(10), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # OIDC.
    oidc_issuer: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    oidc_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Encrypted at rest (SecretBox); never returned by a read projection.
    oidc_client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)

    # SAML.
    saml_entity_id: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    saml_sso_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    #: Encrypted at rest (SecretBox); the IdP's signing certificate.
    saml_x509_cert: Mapped[str | None] = mapped_column(Text, nullable=True)

    jit_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    default_role_key: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="agent"
    )
    allowed_domains: Mapped[list[str]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="[]"
    )
    attribute_mapping: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default="{}"
    )

    #: unconfigured | active | error.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="unconfigured"
    )

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_sso_connections_org"),
        CheckConstraint(
            "protocol IN ('saml', 'oidc')", name="ck_sso_connections_protocol"
        ),
    )

    def __repr__(self) -> str:
        # Never include the secret or certificate.
        return f"<SsoConnection {self.id} {self.protocol} {self.status}>"


class FeatureFlag(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "feature_flags"

    organization_id: Mapped[UUID] = _org_fk()

    key: Mapped[str] = mapped_column(String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_by: Mapped[UUID | None] = _actor_fk()

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("organization_id", "key", name="uq_feature_flags_org_key"),
        Index("ix_feature_flags_org", "organization_id"),
    )
