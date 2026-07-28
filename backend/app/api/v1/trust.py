"""Trust Center & risk management endpoints (Phase 8.4).

The trust officer's surface: the certification-framework catalogue, the risk
register, tracked certifications, the Trust Center profile and questionnaire, the
internal trust dashboard, and the customer-facing overview. Every management
handler is gated on `settings.manage` inside its service; the overview needs only
an authenticated tenant member and exposes no register internals.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    SettingsDep,
    TenantSessionDep,
    verify_csrf,
)
from app.schemas.trust import (
    CertificationCreate,
    CertificationFrameworkRead,
    CertificationRead,
    CertificationUpdate,
    QuestionnaireItemCreate,
    QuestionnaireItemRead,
    QuestionnaireItemUpdate,
    RegisterSummaryRead,
    RiskCreate,
    RiskRead,
    RiskUpdate,
    TrustDashboard,
    TrustOverview,
    TrustProfileRead,
    TrustProfileUpdate,
)
from app.services.trust import (
    CertificationService,
    QuestionnaireService,
    RiskRegisterService,
    TrustCenterService,
    TrustProfileService,
    frameworks_catalogue,
)

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# --------------------------------------------------- catalogue & aggregation


@router.get("/certification-frameworks", response_model=list[CertificationFrameworkRead])
async def list_frameworks(
    _session: TenantSessionDep, _auth: Authorization, _user: CurrentUser
) -> list[CertificationFrameworkRead]:
    return [CertificationFrameworkRead(**f) for f in frameworks_catalogue()]


@router.get("/dashboard", response_model=TrustDashboard)
async def trust_dashboard(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> TrustDashboard:
    return await TrustCenterService(session, auth, settings).dashboard()


@router.get("/overview", response_model=TrustOverview)
async def trust_overview(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> TrustOverview:
    """The customer-facing security overview. Authenticated, but ungated —
    it carries no risk register, failing controls, or internal counts."""
    return await TrustCenterService(session, auth, settings).overview()


# ----------------------------------------------------------- risk register


@router.get("/risks", response_model=list[RiskRead])
async def list_risks(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    risk_status: str | None = None,
    category: str | None = None,
) -> list[RiskRead]:
    return await RiskRegisterService(session, auth).list_risks(
        status=risk_status, category=category
    )


@router.get("/risks/summary", response_model=RegisterSummaryRead)
async def risk_summary(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> RegisterSummaryRead:
    return await RiskRegisterService(session, auth).summary()


@router.post(
    "/risks",
    response_model=RiskRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_risk(
    payload: RiskCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> RiskRead:
    result = await RiskRegisterService(session, auth).create(user, payload)
    await session.commit()
    return result


@router.get("/risks/{risk_id}", response_model=RiskRead)
async def get_risk(
    risk_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> RiskRead:
    return await RiskRegisterService(session, auth).get(risk_id)


@router.put("/risks/{risk_id}", response_model=RiskRead, dependencies=_CSRF)
async def update_risk(
    risk_id: UUID,
    payload: RiskUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> RiskRead:
    result = await RiskRegisterService(session, auth).update(user, risk_id, payload)
    await session.commit()
    return result


@router.delete(
    "/risks/{risk_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def delete_risk(
    risk_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await RiskRegisterService(session, auth).delete(user, risk_id)
    await session.commit()


# ----------------------------------------------------------- certifications


@router.get("/certifications", response_model=list[CertificationRead])
async def list_certifications(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser, settings: SettingsDep
) -> list[CertificationRead]:
    return await CertificationService(session, auth, settings).list_certifications()


@router.post(
    "/certifications",
    response_model=CertificationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_certification(
    payload: CertificationCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> CertificationRead:
    result = await CertificationService(session, auth, settings).create(user, payload)
    await session.commit()
    return result


@router.get("/certifications/{cert_id}", response_model=CertificationRead)
async def get_certification(
    cert_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> CertificationRead:
    return await CertificationService(session, auth, settings).get(cert_id)


@router.put(
    "/certifications/{cert_id}", response_model=CertificationRead, dependencies=_CSRF
)
async def update_certification(
    cert_id: UUID,
    payload: CertificationUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> CertificationRead:
    result = await CertificationService(session, auth, settings).update(
        user, cert_id, payload
    )
    await session.commit()
    return result


@router.delete(
    "/certifications/{cert_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def delete_certification(
    cert_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> None:
    await CertificationService(session, auth, settings).delete(user, cert_id)
    await session.commit()


# ------------------------------------------------------------- profile


@router.get("/profile", response_model=TrustProfileRead)
async def get_profile(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> TrustProfileRead:
    return await TrustProfileService(session, auth).get()


@router.put("/profile", response_model=TrustProfileRead, dependencies=_CSRF)
async def update_profile(
    payload: TrustProfileUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> TrustProfileRead:
    result = await TrustProfileService(session, auth).update(user, payload)
    await session.commit()
    return result


@router.post("/profile/publish", response_model=TrustProfileRead, dependencies=_CSRF)
async def publish_profile(
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    public: bool = True,
) -> TrustProfileRead:
    result = await TrustProfileService(session, auth).publish(user, public=public)
    await session.commit()
    return result


# ----------------------------------------------------------- questionnaire


@router.get("/questionnaire", response_model=list[QuestionnaireItemRead])
async def list_questionnaire(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[QuestionnaireItemRead]:
    return await QuestionnaireService(session, auth).list_items()


@router.post(
    "/questionnaire",
    response_model=QuestionnaireItemRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_questionnaire_item(
    payload: QuestionnaireItemCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> QuestionnaireItemRead:
    result = await QuestionnaireService(session, auth).create(user, payload)
    await session.commit()
    return result


@router.put(
    "/questionnaire/{item_id}",
    response_model=QuestionnaireItemRead,
    dependencies=_CSRF,
)
async def update_questionnaire_item(
    item_id: UUID,
    payload: QuestionnaireItemUpdate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> QuestionnaireItemRead:
    result = await QuestionnaireService(session, auth).update(user, item_id, payload)
    await session.commit()
    return result


@router.delete(
    "/questionnaire/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CSRF,
)
async def delete_questionnaire_item(
    item_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> None:
    await QuestionnaireService(session, auth).delete(user, item_id)
    await session.commit()
