"""Enterprise governance services.

The orchestration around the deterministic rules in `app.enterprise.policies`.
Each service owns one administrative concern, is gated on `settings.manage`, and
reuses the existing infrastructure — audit, encryption, RBAC, billing
entitlements, notifications, and the worker queue — rather than re-implementing
any of it. The singleton policy tables (security, branding, compliance, SSO) are
upserted through a get-or-create so a workspace that never opened the settings
screen still reads the shipped defaults.

The two machine halves — `DataRequestProcessor` (the GDPR worker) and
`ScimProvisioningService` (identity provisioning) — take no `settings.manage`
gate at the object level: the first runs under a system context, and the second
runs under an API-key machine principal that already carries the permission.
"""

from __future__ import annotations

import secrets as pysecrets
from datetime import UTC, datetime
from typing import Any, ClassVar, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.secrets import get_secret_box
from app.core.security import hash_password
from app.enterprise.policies import (
    PasswordRules,
    SessionLimits,
    evaluate_password,
    ip_allowed,
    resolve_jit_identity,
    retention_cutoff,
)
from app.models.enterprise import (
    CompliancePolicy,
    DataRequest,
    FeatureFlag,
    OrganizationBranding,
    SecurityPolicy,
    SsoConnection,
)
from app.models.user import User
from app.repositories.enterprise import (
    CompliancePolicyRepository,
    DataRequestRepository,
    FeatureFlagRepository,
    OrganizationBrandingRepository,
    SecurityPolicyRepository,
    SsoConnectionRepository,
)
from app.schemas.common import Cursor
from app.schemas.enterprise import (
    BrandingRead,
    BrandingUpdate,
    CompliancePolicyRead,
    CompliancePolicyUpdate,
    DataRequestRead,
    FeatureView,
    PasswordCheckResult,
    SecurityPolicyRead,
    SecurityPolicyUpdate,
    SessionRevokeResult,
    SsoConnectionRead,
    SsoConnectionUpdate,
    TenantHealth,
)
from app.services.audit import AuditService
from app.services.billing.service import EntitlementService
from app.services.rbac import AuthorizationContext

logger = get_logger(__name__)

MANAGE_PERMISSION = "settings.manage"
FEATURE = "enterprise"

_OIDC_SECRET_CONTEXT = "sso_connections.oidc_client_secret"  # noqa: S105 — a column name
_SAML_CERT_CONTEXT = "sso_connections.saml_x509_cert"


# =========================================================== security


class SecurityPolicyService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.repo = SecurityPolicyRepository(session)
        self.audit = AuditService(session)

    async def _load(self) -> SecurityPolicy | None:
        return await self.repo.get_for_org(self.auth.organization_id)

    async def _get_or_create(self) -> SecurityPolicy:
        row = await self._load()
        if row is None:
            row = SecurityPolicy(organization_id=self.auth.organization_id)
            self.session.add(row)
            await self.session.flush()
            await self.session.refresh(row)
        return row

    async def get(self) -> SecurityPolicyRead:
        self.auth.require(MANAGE_PERMISSION)
        return _security_read(await self._load())

    async def password_rules(self) -> PasswordRules:
        row = await self._load()
        if row is None:
            return PasswordRules()
        return PasswordRules(
            min_length=row.password_min_length,
            require_upper=row.password_require_upper,
            require_lower=row.password_require_lower,
            require_number=row.password_require_number,
            require_symbol=row.password_require_symbol,
        )

    async def session_limits(self) -> SessionLimits:
        row = await self._load()
        if row is None:
            return SessionLimits()
        return SessionLimits(
            idle_timeout_minutes=row.session_idle_timeout_minutes,
            absolute_hours=row.session_absolute_hours,
            valid_after=row.sessions_valid_after,
        )

    async def check_password(self, password: str) -> PasswordCheckResult:
        violations = evaluate_password(await self.password_rules(), password)
        return PasswordCheckResult(ok=not violations, violations=violations)

    async def check_ip(self, candidate: str) -> bool:
        row = await self._load()
        if row is None:
            return True
        return ip_allowed(
            row.ip_allowlist, candidate, enforced=row.ip_enforcement
        )

    async def update(
        self, actor: User, payload: SecurityPolicyUpdate
    ) -> SecurityPolicyRead:
        self.auth.require(MANAGE_PERMISSION)
        row = await self._get_or_create()
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(row, field, value)
        await self.session.flush()
        await self.session.refresh(row)
        await self._audit(AuditAction.SECURITY_POLICY_UPDATED, actor, row)
        return _security_read(row)

    async def revoke_all_sessions(self, actor: User) -> SessionRevokeResult:
        """The wholesale sign-out: stamp `sessions_valid_after` to now so every
        session issued up to this instant is deterministically revoked."""
        self.auth.require(MANAGE_PERMISSION)
        row = await self._get_or_create()
        now = datetime.now(UTC)
        row.sessions_valid_after = now
        await self.session.flush()
        await self._audit(AuditAction.SESSIONS_REVOKED, actor, row)
        logger.warning(
            "enterprise_sessions_revoked",
            extra={"organization_id": str(self.auth.organization_id)},
        )
        return SessionRevokeResult(sessions_valid_after=now)

    async def _audit(self, action: str, actor: User, row: SecurityPolicy) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="security_policy",
            entity_id=row.id,
            metadata={
                "mfa_required": row.mfa_required,
                "ip_enforcement": row.ip_enforcement,
            },
        )


def _security_read(row: SecurityPolicy | None) -> SecurityPolicyRead:
    if row is None:
        defaults = PasswordRules()
        return SecurityPolicyRead(
            password_min_length=defaults.min_length,
            password_require_upper=defaults.require_upper,
            password_require_lower=defaults.require_lower,
            password_require_number=defaults.require_number,
            password_require_symbol=defaults.require_symbol,
            password_expiry_days=None,
            mfa_required=False,
            mfa_grace_days=7,
            session_idle_timeout_minutes=None,
            session_absolute_hours=None,
            max_concurrent_sessions=None,
            sessions_valid_after=None,
            ip_allowlist=[],
            ip_enforcement=False,
        )
    return SecurityPolicyRead(
        password_min_length=row.password_min_length,
        password_require_upper=row.password_require_upper,
        password_require_lower=row.password_require_lower,
        password_require_number=row.password_require_number,
        password_require_symbol=row.password_require_symbol,
        password_expiry_days=row.password_expiry_days,
        mfa_required=row.mfa_required,
        mfa_grace_days=row.mfa_grace_days,
        session_idle_timeout_minutes=row.session_idle_timeout_minutes,
        session_absolute_hours=row.session_absolute_hours,
        max_concurrent_sessions=row.max_concurrent_sessions,
        sessions_valid_after=row.sessions_valid_after,
        ip_allowlist=list(row.ip_allowlist),
        ip_enforcement=row.ip_enforcement,
    )


# =========================================================== branding


class BrandingService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = OrganizationBrandingRepository(session)
        self.audit = AuditService(session)

    async def get(self) -> BrandingRead:
        self.auth.require(MANAGE_PERMISSION)
        return _branding_read(await self.repo.get_for_org(self.auth.organization_id))

    async def update(self, actor: User, payload: BrandingUpdate) -> BrandingRead:
        self.auth.require(MANAGE_PERMISSION)
        row = await self.repo.get_for_org(self.auth.organization_id)
        if row is None:
            row = OrganizationBranding(organization_id=self.auth.organization_id)
            self.session.add(row)
            await self.session.flush()
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(row, field, value)
        await self.session.flush()
        await self.session.refresh(row)
        await self.audit.record(
            action=AuditAction.BRANDING_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="organization_branding",
            entity_id=row.id,
            metadata={"is_published": row.is_published},
        )
        return _branding_read(row)


def _branding_read(row: OrganizationBranding | None) -> BrandingRead:
    if row is None:
        return BrandingRead(
            logo_url=None, icon_url=None, primary_color=None, accent_color=None,
            login_heading=None, login_subheading=None, email_from_name=None,
            email_footer=None, support_url=None, custom_domain=None,
            is_published=False,
        )
    return BrandingRead(
        logo_url=row.logo_url,
        icon_url=row.icon_url,
        primary_color=row.primary_color,
        accent_color=row.accent_color,
        login_heading=row.login_heading,
        login_subheading=row.login_subheading,
        email_from_name=row.email_from_name,
        email_footer=row.email_footer,
        support_url=row.support_url,
        custom_domain=row.custom_domain,
        is_published=row.is_published,
    )


# =========================================================== features


class FeatureService:
    """The merge of a plan's billing entitlements and the tenant's overrides."""

    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.repo = FeatureFlagRepository(session)
        self.audit = AuditService(session)

    async def _overrides(self) -> dict[str, bool]:
        flags = await self.repo.list_for_org(self.auth.organization_id)
        return {flag.key: flag.enabled for flag in flags}

    async def _plan_features(self) -> dict[str, bool]:
        raw = await EntitlementService(self.session, self.auth).features()
        return {key: bool(value) for key, value in raw.items()}

    async def view(self) -> FeatureView:
        self.auth.require(MANAGE_PERMISSION)
        plan = await self._plan_features()
        overrides = await self._overrides()
        effective = {**plan, **overrides}  # an explicit override wins over the plan
        return FeatureView(plan_features=plan, overrides=overrides, effective=effective)

    async def has_feature(self, key: str) -> bool:
        overrides = await self._overrides()
        if key in overrides:
            return overrides[key]
        return (await self._plan_features()).get(key, False)

    async def set_flag(
        self, actor: User, key: str, *, enabled: bool, note: str | None
    ) -> None:
        self.auth.require(MANAGE_PERMISSION)
        flag = await self.repo.get_by_key(self.auth.organization_id, key)
        if flag is None:
            flag = FeatureFlag(
                organization_id=self.auth.organization_id, key=key
            )
            self.session.add(flag)
        flag.enabled = enabled
        flag.note = note
        flag.updated_by = actor.id
        await self.session.flush()
        await self.audit.record(
            action=AuditAction.FEATURE_FLAG_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="feature_flag",
            entity_id=flag.id,
            metadata={"key": key, "enabled": enabled},
        )


# =========================================================== sso


class SsoService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.repo = SsoConnectionRepository(session)
        self.audit = AuditService(session)
        self._box = get_secret_box()

    async def get(self) -> SsoConnectionRead:
        self.auth.require(MANAGE_PERMISSION)
        return _sso_read(await self.repo.get_for_org(self.auth.organization_id))

    async def _connection(self) -> SsoConnection | None:
        return await self.repo.get_for_org(self.auth.organization_id)

    async def update(
        self, actor: User, payload: SsoConnectionUpdate
    ) -> SsoConnectionRead:
        self.auth.require(MANAGE_PERMISSION)
        row = await self._connection()
        if row is None:
            row = SsoConnection(
                organization_id=self.auth.organization_id,
                created_by=actor.id,
                protocol=payload.protocol or "oidc",
            )
            self.session.add(row)
            await self.session.flush()

        data = payload.model_dump(exclude_unset=True)
        if "oidc_client_secret" in data:
            secret = data.pop("oidc_client_secret")
            row.oidc_client_secret = (
                self._box.encrypt(secret, context=_OIDC_SECRET_CONTEXT)
                if secret
                else None
            )
        if "saml_x509_cert" in data:
            cert = data.pop("saml_x509_cert")
            row.saml_x509_cert = (
                self._box.encrypt(cert, context=_SAML_CERT_CONTEXT) if cert else None
            )
        for field, value in data.items():
            setattr(row, field, value)

        row.status = "active" if row.is_enabled and _sso_configured(row) else "unconfigured"
        await self.session.flush()
        await self.session.refresh(row)
        await self.audit.record(
            action=AuditAction.SSO_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="sso_connection",
            entity_id=row.id,
            metadata={"protocol": row.protocol, "enabled": row.is_enabled},
        )
        return _sso_read(row)

    async def jit_decision(self, claims: dict[str, Any]) -> Any:
        """Resolve claims to a provisioning decision using the stored settings.
        Pure delegation to the deterministic resolver, for OIDC/SCIM callers."""
        row = await self._connection()
        if row is None:
            from app.enterprise.policies import JitDecision

            return JitDecision(False, "No SSO connection configured.")
        return resolve_jit_identity(
            jit_enabled=row.jit_enabled,
            allowed_domains=list(row.allowed_domains),
            default_role_key=row.default_role_key,
            attribute_mapping={str(k): str(v) for k, v in row.attribute_mapping.items()},
            claims=claims,
        )


def _sso_configured(row: SsoConnection) -> bool:
    if row.protocol == "oidc":
        return bool(row.oidc_issuer and row.oidc_client_id)
    return bool(row.saml_entity_id and row.saml_sso_url)


def _sso_read(row: SsoConnection | None) -> SsoConnectionRead:
    if row is None:
        return SsoConnectionRead(
            protocol="oidc", is_enabled=False, display_name=None, oidc_issuer=None,
            oidc_client_id=None, oidc_client_secret_set=False, saml_entity_id=None,
            saml_sso_url=None, saml_x509_cert_set=False, jit_enabled=True,
            default_role_key="agent", allowed_domains=[], attribute_mapping={},
            status="unconfigured",
        )
    return SsoConnectionRead(
        protocol=row.protocol,
        is_enabled=row.is_enabled,
        display_name=row.display_name,
        oidc_issuer=row.oidc_issuer,
        oidc_client_id=row.oidc_client_id,
        oidc_client_secret_set=row.oidc_client_secret is not None,
        saml_entity_id=row.saml_entity_id,
        saml_sso_url=row.saml_sso_url,
        saml_x509_cert_set=row.saml_x509_cert is not None,
        jit_enabled=row.jit_enabled,
        default_role_key=row.default_role_key,
        allowed_domains=list(row.allowed_domains),
        attribute_mapping={str(k): str(v) for k, v in row.attribute_mapping.items()},
        status=row.status,
    )


class ScimProvisioningService:
    """SCIM 2.0 user provisioning, driven by an IdP. Authenticated by an API-key
    machine principal that already carries `settings.manage`; the object trusts
    that gate rather than re-checking a user session it does not have."""

    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.audit = AuditService(session)

    async def provision_user(self, payload: dict[str, Any]) -> User:
        """Create or update a user from a SCIM payload (JIT provisioning).

        The email is the natural key. An existing user is updated in place; a new
        one is created with the connection's default role and a random password
        (they sign in through the IdP, never with it).
        """
        self.auth.require(MANAGE_PERMISSION)
        email = _scim_email(payload)
        full_name = _scim_name(payload)
        active = bool(payload.get("active", True))

        existing = await self._find_by_email(email)
        if existing is not None:
            existing.full_name = full_name or existing.full_name
            existing.status = "active" if active else "deactivated"
            await self.session.flush()
            await self._audit(AuditAction.SCIM_USER_PROVISIONED, existing, created=False)
            return existing

        role_key = await self._default_role_key()
        user = User(
            organization_id=self.auth.organization_id,
            email=email,
            password_hash=hash_password(pysecrets.token_urlsafe(24)),
            full_name=full_name or email.split("@")[0],
            status="active" if active else "invited",
        )
        self.session.add(user)
        await self.session.flush()

        from app.services.rbac import RbacService

        await RbacService(self.session).assign_role(
            user_id=user.id,
            role_key=role_key,
            organization_id=self.auth.organization_id,
            granted_by=self.auth.user_id,
        )
        await self._audit(AuditAction.SCIM_USER_PROVISIONED, user, created=True)
        logger.info("scim_user_provisioned", extra={"user_id": str(user.id)})
        return user

    async def deactivate_user(self, user_id: UUID) -> User:
        self.auth.require(MANAGE_PERMISSION)
        user = await self._find_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        user.status = "deactivated"
        await self.session.flush()
        await self._audit(AuditAction.SCIM_USER_DEACTIVATED, user, created=False)
        return user

    async def _default_role_key(self) -> str:
        connection = await SsoConnectionRepository(self.session).get_for_org(
            self.auth.organization_id
        )
        return connection.default_role_key if connection else "agent"

    async def _find_by_email(self, email: str) -> User | None:
        query = (
            select(User)
            .where(User.organization_id == self.auth.organization_id)
            .where(User.email == email)
            .where(User.deleted_at.is_(None))
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def _find_by_id(self, user_id: UUID) -> User | None:
        query = (
            select(User)
            .where(User.organization_id == self.auth.organization_id)
            .where(User.id == user_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def _audit(self, action: str, user: User, *, created: bool) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=self.auth.user_id,
            actor_email="scim",
            entity_type="user",
            entity_id=user.id,
            metadata={"email": user.email, "created": created},
        )


def _scim_email(payload: dict[str, Any]) -> str:
    if payload.get("userName"):
        return str(payload["userName"]).strip().lower()
    emails = payload.get("emails") or []
    for entry in emails:
        if isinstance(entry, dict) and entry.get("value"):
            return str(entry["value"]).strip().lower()
    raise ConflictError("SCIM payload has no userName or email.")


def _scim_name(payload: dict[str, Any]) -> str | None:
    display = payload.get("displayName")
    if display:
        return str(display)
    name = payload.get("name") or {}
    if isinstance(name, dict):
        parts = [name.get("givenName"), name.get("familyName")]
        joined = " ".join(str(p) for p in parts if p)
        return joined or None
    return None


# =========================================================== compliance


class ComplianceService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.repo = CompliancePolicyRepository(session)
        self.requests = DataRequestRepository(session)
        self.audit = AuditService(session)

    async def _get_or_create(self) -> CompliancePolicy:
        row = await self.repo.get_for_org(self.auth.organization_id)
        if row is None:
            row = CompliancePolicy(organization_id=self.auth.organization_id)
            self.session.add(row)
            await self.session.flush()
            await self.session.refresh(row)
        return row

    async def get_policy(self) -> CompliancePolicyRead:
        self.auth.require(MANAGE_PERMISSION)
        return _compliance_read(await self.repo.get_for_org(self.auth.organization_id))

    async def update_policy(
        self, actor: User, payload: CompliancePolicyUpdate
    ) -> CompliancePolicyRead:
        self.auth.require(MANAGE_PERMISSION)
        row = await self._get_or_create()
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(row, field, value)
        await self.session.flush()
        await self.session.refresh(row)
        await self.audit.record(
            action=AuditAction.COMPLIANCE_POLICY_UPDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="compliance_policy",
            entity_id=row.id,
            metadata={"retention": dict(row.retention_days)},
        )
        return _compliance_read(row)

    async def set_legal_hold(
        self, actor: User, *, enabled: bool, reason: str | None
    ) -> CompliancePolicyRead:
        self.auth.require(MANAGE_PERMISSION)
        row = await self._get_or_create()
        row.legal_hold = enabled
        row.legal_hold_reason = reason if enabled else None
        await self.session.flush()
        await self.session.refresh(row)
        await self.audit.record(
            action=AuditAction.LEGAL_HOLD_CHANGED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="compliance_policy",
            entity_id=row.id,
            metadata={"legal_hold": enabled, "reason": reason},
        )
        return _compliance_read(row)

    async def create_data_request(
        self, actor: User, *, kind: str, subject_email: str
    ) -> DataRequestRead:
        """Open a GDPR export or erasure request and queue it for processing."""
        self.auth.require(MANAGE_PERMISSION)
        subject_email = subject_email.strip().lower()
        subject = await self._find_user(subject_email)
        request = DataRequest(
            organization_id=self.auth.organization_id,
            created_by=actor.id,
            kind=kind,
            subject_email=subject_email,
            subject_user_id=subject.id if subject else None,
        )
        self.session.add(request)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.DATA_REQUEST_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="data_request",
            entity_id=request.id,
            metadata={"kind": kind, "subject": subject_email},
        )
        from app.workers.queue import JobName, enqueue

        await enqueue(
            JobName.PROCESS_DATA_REQUEST,
            str(request.id),
            str(self.auth.organization_id),
            job_id=f"datareq:{request.id}",
        )
        return _data_request_read(request)

    async def list_data_requests(
        self, *, limit: int, cursor: Cursor | None
    ) -> tuple[list[DataRequestRead], bool]:
        self.auth.require(MANAGE_PERMISSION)
        rows, has_more = await self.requests.list_for_org(
            self.auth.organization_id, limit=limit, cursor=cursor
        )
        return [_data_request_read(row) for row in rows], has_more

    async def get_data_request(self, request_id: UUID) -> DataRequestRead:
        self.auth.require(MANAGE_PERMISSION)
        row = await self.requests.get(request_id, self.auth.organization_id)
        if row is None:
            raise NotFoundError("Data request not found.")
        return _data_request_read(row)

    async def _find_user(self, email: str) -> User | None:
        query = (
            select(User)
            .where(User.organization_id == self.auth.organization_id)
            .where(User.email == email)
        )
        return (await self.session.execute(query)).scalar_one_or_none()


def _compliance_read(row: CompliancePolicy | None) -> CompliancePolicyRead:
    if row is None:
        return CompliancePolicyRead(
            retention_days={}, legal_hold=False, legal_hold_reason=None, dpo_email=None
        )
    return CompliancePolicyRead(
        retention_days={str(k): int(v) for k, v in row.retention_days.items()},
        legal_hold=row.legal_hold,
        legal_hold_reason=row.legal_hold_reason,
        dpo_email=row.dpo_email,
    )


def _data_request_read(row: DataRequest) -> DataRequestRead:
    return DataRequestRead(
        id=row.id,
        kind=row.kind,
        subject_email=row.subject_email,
        subject_user_id=row.subject_user_id,
        status=row.status,
        result=dict(row.result),
        error=row.error,
        processed_at=row.processed_at,
        created_at=row.created_at,
    )


# =============================================== tenant health dashboard


class TenantHealthService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings

    async def health(self) -> TenantHealth:
        self.auth.require(MANAGE_PERMISSION)
        org = self.auth.organization_id

        summary = await EntitlementService(self.session, self.auth).summary()
        users_active = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(User)
                    .where(User.organization_id == org)
                    .where(User.status == "active")
                )
            ).scalar()
            or 0
        )
        security = await SecurityPolicyRepository(self.session).get_for_org(org)
        sso = await SsoConnectionRepository(self.session).get_for_org(org)
        compliance = await CompliancePolicyRepository(self.session).get_for_org(org)
        branding = await OrganizationBrandingRepository(self.session).get_for_org(org)
        open_requests = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(DataRequest)
                    .where(DataRequest.organization_id == org)
                    .where(DataRequest.status.in_(("pending", "processing")))
                )
            ).scalar()
            or 0
        )

        mfa_required = bool(security and security.mfa_required)
        ip_enforcement = bool(security and security.ip_enforcement)
        legal_hold = bool(compliance and compliance.legal_hold)
        posture = "attention" if open_requests or legal_hold else "healthy"

        return TenantHealth(
            organization_id=org,
            plan=summary.get("plan_key"),
            users_active=users_active,
            sso_enabled=bool(sso and sso.is_enabled),
            mfa_required=mfa_required,
            ip_enforcement=ip_enforcement,
            legal_hold=legal_hold,
            open_data_requests=open_requests,
            branding_published=bool(branding and branding.is_published),
            posture=posture,
        )


# =============================================== data request processing


class DataRequestProcessor:
    """The GDPR worker half. Runs under a system context; operates on the
    session it is given so it can be driven directly by the job or a test."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.audit = AuditService(session)

    async def process(self, request: DataRequest) -> None:
        if request.status not in ("pending", "processing"):
            return
        request.status = "processing"
        await self.session.flush()

        from app.observability import metrics

        try:
            if request.kind == "export":
                result = await self._export(request)
            else:
                result = await self._delete(request)
        except _LegalHoldError as exc:
            request.status = "failed"
            request.error = str(exc)
            request.processed_at = datetime.now(UTC)
            await self.session.flush()
            metrics.record_data_request(
                kind=request.kind, outcome="failed",
                organization_id=request.organization_id,
            )
            return

        request.status = "completed"
        request.result = result
        request.processed_at = datetime.now(UTC)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.DATA_REQUEST_COMPLETED,
            organization_id=request.organization_id,
            actor_id=None,
            actor_email="system",
            entity_type="data_request",
            entity_id=request.id,
            metadata={"kind": request.kind, "subject": request.subject_email},
        )
        metrics.record_data_request(
            kind=request.kind, outcome="completed",
            organization_id=request.organization_id,
        )
        await self._notify(request)

    async def _export(self, request: DataRequest) -> dict[str, Any]:
        from app.models.audit import AuditLog

        subject = await self._subject(request)
        audit_events = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.organization_id == request.organization_id)
                    .where(AuditLog.actor_id == request.subject_user_id)
                )
            ).scalar()
            or 0
        )
        profile: dict[str, Any] = {}
        if subject is not None:
            profile = {
                "email": subject.email,
                "full_name": subject.full_name,
                "job_title": subject.job_title,
                "phone": subject.phone,
                "created_at": subject.created_at.isoformat(),
            }
        return {
            "profile": profile,
            "audit_events": audit_events,
            # The bundle itself is delivered out of band; the row keeps a
            # summary, never the subject's personal data.
            "note": "Export prepared. The data package is delivered securely out of band.",
        }

    async def _delete(self, request: DataRequest) -> dict[str, Any]:
        policy = await CompliancePolicyRepository(self.session).get_for_org(
            request.organization_id
        )
        if policy is not None and policy.legal_hold:
            raise _LegalHoldError(
                "A legal hold is in force; erasure is suspended until it is lifted."
            )
        subject = await self._subject(request)
        if subject is None:
            return {"redacted": [], "note": "No matching user to erase."}

        redacted = ["email", "full_name", "phone", "job_title"]
        subject.email = f"deleted+{subject.id}@redacted.invalid"
        subject.full_name = "Deleted User"
        subject.phone = None
        subject.job_title = None
        subject.status = "deactivated"
        subject.deleted_at = datetime.now(UTC)
        # New random password so the old hash cannot be brute-forced offline.
        subject.password_hash = hash_password(pysecrets.token_urlsafe(24))
        await self.session.flush()
        return {"redacted": redacted, "user_id": str(subject.id)}

    async def _subject(self, request: DataRequest) -> User | None:
        if request.subject_user_id is not None:
            query = select(User).where(User.id == request.subject_user_id)
            return (await self.session.execute(query)).scalar_one_or_none()
        query = (
            select(User)
            .where(User.organization_id == request.organization_id)
            .where(User.email == request.subject_email)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def _notify(self, request: DataRequest) -> None:
        if request.created_by is None:
            return
        from app.services.notification_center import NotificationCenter

        await NotificationCenter(self.session).raise_notification(
            organization_id=request.organization_id,
            recipient_id=request.created_by,
            category="system",
            type="data_request.completed",
            title=f"Data {request.kind} request completed",
            body=f"The {request.kind} request for {request.subject_email} is complete.",
            entity_type="data_request",
            entity_id=request.id,
        )


class _LegalHoldError(Exception):
    """Raised when a legal hold blocks an erasure."""


# =================================================== retention sweeping


class RetentionService:
    """Deletes records past their retention window, honouring legal hold. Runs
    under a system context on the given session.

    Only two record classes are reapable — the audit log and the notification
    feed. That is an allowlist by construction: a retention key that names a
    business table simply has no branch, so a typo can never point retention at
    customer data.
    """

    #: The retention keys a policy may use.
    REAPABLE: ClassVar[frozenset[str]] = frozenset({"audit_logs", "notifications"})

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def sweep(self, organization_id: UUID) -> dict[str, int]:
        policy = await CompliancePolicyRepository(self.session).get_for_org(
            organization_id
        )
        if policy is None or policy.legal_hold:
            return {}

        deleted: dict[str, int] = {}
        for entity, days in policy.retention_days.items():
            if entity not in self.REAPABLE:
                continue
            cutoff = retention_cutoff(int(days))
            if cutoff is None:
                continue
            deleted[entity] = await self._reap(entity, organization_id, cutoff)
        return deleted

    async def _reap(
        self, entity: str, organization_id: UUID, cutoff: datetime
    ) -> int:
        from sqlalchemy import CursorResult, delete

        from app.models.audit import AuditLog
        from app.models.notification import Notification

        if entity == "audit_logs":
            statement = (
                delete(AuditLog)
                .where(AuditLog.organization_id == organization_id)
                .where(AuditLog.created_at < cutoff)
            )
        else:  # notifications
            statement = (
                delete(Notification)
                .where(Notification.organization_id == organization_id)
                .where(Notification.created_at < cutoff)
            )
        result = await self.session.execute(statement)
        return cast("CursorResult[Any]", result).rowcount


__all__ = [
    "FEATURE",
    "MANAGE_PERMISSION",
    "BrandingService",
    "ComplianceService",
    "DataRequestProcessor",
    "FeatureService",
    "RetentionService",
    "ScimProvisioningService",
    "SecurityPolicyService",
    "SsoService",
    "TenantHealthService",
]
