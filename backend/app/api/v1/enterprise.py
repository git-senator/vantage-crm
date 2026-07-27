"""Enterprise governance endpoints (Phase 8.0).

The signed-in administrative surface for security policy, white-label branding,
compliance/GDPR, SSO configuration, feature management, and the tenant-health
dashboard — every handler gated on `settings.manage` inside its service. The
SCIM sub-surface is the exception: it is machine-authenticated with an API key
(Phase 7.1), the standard way an identity provider provisions users.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    MachineAuth,
    SettingsDep,
    TenantSessionDep,
    verify_csrf,
)
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Cursor, Page, PageMeta
from app.schemas.enterprise import (
    BrandingRead,
    BrandingUpdate,
    CompliancePolicyRead,
    CompliancePolicyUpdate,
    DataRequestCreate,
    DataRequestRead,
    FeatureFlagUpdate,
    FeatureView,
    LegalHoldUpdate,
    PasswordCheck,
    PasswordCheckResult,
    SecurityPolicyRead,
    SecurityPolicyUpdate,
    SessionRevokeResult,
    SsoConnectionRead,
    SsoConnectionUpdate,
    TenantHealth,
)
from app.services.audit import AuditService
from app.services.enterprise import (
    BrandingService,
    ComplianceService,
    FeatureService,
    ScimProvisioningService,
    SecurityPolicyService,
    SsoService,
    TenantHealthService,
)

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# ------------------------------------------------------------- security


@router.get("/security-policy", response_model=SecurityPolicyRead)
async def get_security_policy(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> SecurityPolicyRead:
    return await SecurityPolicyService(session, auth, settings).get()


@router.put("/security-policy", response_model=SecurityPolicyRead, dependencies=_CSRF)
async def update_security_policy(
    payload: SecurityPolicyUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> SecurityPolicyRead:
    result = await SecurityPolicyService(session, auth, settings).update(user, payload)
    await session.commit()
    return result


@router.post(
    "/security-policy/check-password",
    response_model=PasswordCheckResult,
    dependencies=_CSRF,
)
async def check_password(
    payload: PasswordCheck,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> PasswordCheckResult:
    return await SecurityPolicyService(session, auth, settings).check_password(
        payload.password
    )


@router.post(
    "/security-policy/revoke-sessions",
    response_model=SessionRevokeResult,
    dependencies=_CSRF,
)
async def revoke_sessions(
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> SessionRevokeResult:
    result = await SecurityPolicyService(session, auth, settings).revoke_all_sessions(user)
    await session.commit()
    return result


# ------------------------------------------------------------- branding


@router.get("/branding", response_model=BrandingRead)
async def get_branding(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> BrandingRead:
    return await BrandingService(session, auth).get()


@router.put("/branding", response_model=BrandingRead, dependencies=_CSRF)
async def update_branding(
    payload: BrandingUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> BrandingRead:
    result = await BrandingService(session, auth).update(user, payload)
    await session.commit()
    return result


# ----------------------------------------------------------- compliance


@router.get("/compliance", response_model=CompliancePolicyRead)
async def get_compliance(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> CompliancePolicyRead:
    return await ComplianceService(session, auth, settings).get_policy()


@router.put("/compliance", response_model=CompliancePolicyRead, dependencies=_CSRF)
async def update_compliance(
    payload: CompliancePolicyUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> CompliancePolicyRead:
    result = await ComplianceService(session, auth, settings).update_policy(user, payload)
    await session.commit()
    return result


@router.put("/compliance/legal-hold", response_model=CompliancePolicyRead, dependencies=_CSRF)
async def set_legal_hold(
    payload: LegalHoldUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> CompliancePolicyRead:
    result = await ComplianceService(session, auth, settings).set_legal_hold(
        user, enabled=payload.enabled, reason=payload.reason
    )
    await session.commit()
    return result


@router.post(
    "/data-requests",
    response_model=DataRequestRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_CSRF,
)
async def create_data_request(
    payload: DataRequestCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> DataRequestRead:
    result = await ComplianceService(session, auth, settings).create_data_request(
        user, kind=payload.kind, subject_email=payload.subject_email
    )
    await session.commit()
    return result


@router.get("/data-requests", response_model=Page[DataRequestRead])
async def list_data_requests(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> Page[DataRequestRead]:
    rows, has_more = await ComplianceService(session, auth, settings).list_data_requests(
        limit=limit, cursor=Cursor.decode(cursor) if cursor else None
    )
    next_cursor = (
        Cursor(created_at=rows[-1].created_at, id=rows[-1].id).encode()
        if rows and has_more
        else None
    )
    return Page[DataRequestRead](
        data=rows, meta=PageMeta(next_cursor=next_cursor, has_more=has_more, limit=limit)
    )


@router.get("/data-requests/{request_id}", response_model=DataRequestRead)
async def get_data_request(
    request_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> DataRequestRead:
    return await ComplianceService(session, auth, settings).get_data_request(request_id)


@router.get("/audit-export")
async def audit_export(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> list[dict[str, Any]]:
    """A flat JSON export of the audit log for compliance archival."""
    auth.require("settings.manage")
    entries = await AuditService(session).list_entries(auth.organization_id, limit=limit)
    return [
        {
            "id": str(entry.id),
            "action": entry.action,
            "actor_id": str(entry.actor_id) if entry.actor_id else None,
            "actor_email": entry.actor_email,
            "entity_type": entry.entity_type,
            "entity_id": str(entry.entity_id) if entry.entity_id else None,
            "metadata": entry.metadata_,
            "created_at": entry.created_at.isoformat(),
        }
        for entry in entries
    ]


# ------------------------------------------------------------------ sso


@router.get("/sso", response_model=SsoConnectionRead)
async def get_sso(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> SsoConnectionRead:
    return await SsoService(session, auth, settings).get()


@router.put("/sso", response_model=SsoConnectionRead, dependencies=_CSRF)
async def update_sso(
    payload: SsoConnectionUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> SsoConnectionRead:
    result = await SsoService(session, auth, settings).update(user, payload)
    await session.commit()
    return result


# -------------------------------------------------------------- features


@router.get("/features", response_model=FeatureView)
async def get_features(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> FeatureView:
    return await FeatureService(session, auth, settings).view()


@router.put("/features/{key}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_CSRF)
async def set_feature_flag(
    key: str,
    payload: FeatureFlagUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> None:
    await FeatureService(session, auth, settings).set_flag(
        user, key, enabled=payload.enabled, note=payload.note
    )
    await session.commit()


# ---------------------------------------------------------------- health


@router.get("/health", response_model=TenantHealth)
async def tenant_health(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> TenantHealth:
    return await TenantHealthService(session, auth, settings).health()


# ------------------------------------------------------------------ scim
# Machine-authenticated (API key), the standard IdP provisioning path.


@router.post("/scim/v2/Users", status_code=status.HTTP_201_CREATED)
async def scim_create_user(
    payload: dict[str, Any],
    principal: MachineAuth,
    settings: SettingsDep,
) -> dict[str, Any]:
    user = await ScimProvisioningService(
        principal.session, principal.auth, settings
    ).provision_user(payload)
    await principal.session.commit()
    return _scim_user(user)


@router.delete("/scim/v2/Users/{user_id}", status_code=status.HTTP_200_OK)
async def scim_deactivate_user(
    user_id: UUID,
    principal: MachineAuth,
    settings: SettingsDep,
) -> dict[str, Any]:
    user = await ScimProvisioningService(
        principal.session, principal.auth, settings
    ).deactivate_user(user_id)
    await principal.session.commit()
    return _scim_user(user)


def _scim_user(user: Any) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(user.id),
        "userName": user.email,
        "displayName": user.full_name,
        "active": user.is_active,
    }
