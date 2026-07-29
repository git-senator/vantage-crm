"""Marketplace developer platform endpoints (Phase 9.5).

The developer-facing surface, mounted under ``/marketplace``:

  * ``/developers`` — register a developer organization, read a profile, list a
    developer's applications.
  * ``/applications`` — register an application, submit it for review, read its
    status, list its review history, and drive the rest of its lifecycle.
  * ``/reviews`` — the review queue and approve/reject decisions.
  * ``/credentials`` — create, rotate, revoke and list developer API credentials.

Every handler is gated on ``settings.manage`` inside its service. A credential's
raw secret is returned exactly once, from the create/rotate endpoints.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    TenantSessionDep,
    verify_csrf,
)
from app.schemas.marketplace_developer import (
    ApplicationRead,
    ApplicationRegister,
    ApplicationReviewRead,
    CredentialCreate,
    CredentialCreateResult,
    CredentialRead,
    DeveloperOrganizationCreate,
    DeveloperOrganizationRead,
    ReviewDecision,
)
from app.services.marketplace_developer import (
    ApplicationPublishingService,
    ApplicationReviewService,
    DeveloperCredentialService,
    MarketplaceDeveloperService,
)

router = APIRouter()

_CSRF = [Depends(verify_csrf)]


# ------------------------------------------------------- developers


@router.post(
    "/developers",
    response_model=DeveloperOrganizationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_developer(
    payload: DeveloperOrganizationCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> DeveloperOrganizationRead:
    result = await MarketplaceDeveloperService(session, auth).create_developer(
        user, payload
    )
    await session.commit()
    return result


@router.get("/developers", response_model=list[DeveloperOrganizationRead])
async def list_developers(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[DeveloperOrganizationRead]:
    return await MarketplaceDeveloperService(session, auth).list_developers()


@router.get("/developers/{developer_org_id}", response_model=DeveloperOrganizationRead)
async def developer_profile(
    developer_org_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> DeveloperOrganizationRead:
    return await MarketplaceDeveloperService(session, auth).get_developer(
        developer_org_id
    )


@router.get(
    "/developers/{developer_org_id}/applications",
    response_model=list[ApplicationRead],
)
async def developer_applications(
    developer_org_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> list[ApplicationRead]:
    return await MarketplaceDeveloperService(session, auth).list_applications(
        developer_org_id
    )


# ------------------------------------------------------- applications


@router.post(
    "/applications",
    response_model=ApplicationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def register_application(
    payload: ApplicationRegister,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ApplicationRead:
    result = await MarketplaceDeveloperService(session, auth).register_application(
        user, payload
    )
    await session.commit()
    return result


@router.get("/applications/{application_id}", response_model=ApplicationRead)
async def application_status(
    application_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> ApplicationRead:
    return await ApplicationPublishingService(session, auth).status(application_id)


@router.get(
    "/applications/{application_id}/versions",
    response_model=list[ApplicationReviewRead],
)
async def application_versions(
    application_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> list[ApplicationReviewRead]:
    """The application's review history, one entry per reviewed version."""
    return await ApplicationReviewService(session, auth).list_reviews(application_id)


@router.post(
    "/applications/{application_id}/submit",
    response_model=ApplicationRead,
    dependencies=_CSRF,
)
async def submit_application(
    application_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ApplicationRead:
    result = await ApplicationPublishingService(session, auth).submit(
        user, application_id
    )
    await session.commit()
    return result


@router.post(
    "/applications/{application_id}/publish",
    response_model=ApplicationRead,
    dependencies=_CSRF,
)
async def publish_application(
    application_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ApplicationRead:
    result = await ApplicationPublishingService(session, auth).publish(
        user, application_id
    )
    await session.commit()
    return result


@router.post(
    "/applications/{application_id}/deprecate",
    response_model=ApplicationRead,
    dependencies=_CSRF,
)
async def deprecate_application(
    application_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ApplicationRead:
    result = await ApplicationPublishingService(session, auth).deprecate(
        user, application_id
    )
    await session.commit()
    return result


@router.post(
    "/applications/{application_id}/retire",
    response_model=ApplicationRead,
    dependencies=_CSRF,
)
async def retire_application(
    application_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ApplicationRead:
    result = await ApplicationPublishingService(session, auth).retire(
        user, application_id
    )
    await session.commit()
    return result


# ------------------------------------------------------- reviews


@router.get("/reviews/queue", response_model=list[ApplicationRead])
async def review_queue(
    session: TenantSessionDep, auth: Authorization, _user: CurrentUser
) -> list[ApplicationRead]:
    return await ApplicationReviewService(session, auth).review_queue()


@router.post(
    "/reviews/{application_id}/approve",
    response_model=ApplicationReviewRead,
    dependencies=_CSRF,
)
async def approve_application(
    application_id: UUID,
    payload: ReviewDecision,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ApplicationReviewRead:
    result = await ApplicationReviewService(session, auth).approve(
        user, application_id, payload.version, payload.notes
    )
    await session.commit()
    return result


@router.post(
    "/reviews/{application_id}/reject",
    response_model=ApplicationReviewRead,
    dependencies=_CSRF,
)
async def reject_application(
    application_id: UUID,
    payload: ReviewDecision,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> ApplicationReviewRead:
    result = await ApplicationReviewService(session, auth).reject(
        user, application_id, payload.version, payload.notes
    )
    await session.commit()
    return result


# ------------------------------------------------------- credentials


@router.post(
    "/credentials",
    response_model=CredentialCreateResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=_CSRF,
)
async def create_credential(
    payload: CredentialCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> CredentialCreateResult:
    result = await DeveloperCredentialService(session, auth).create(user, payload)
    await session.commit()
    return result


@router.get("/credentials", response_model=list[CredentialRead])
async def list_credentials(
    developer_org_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> list[CredentialRead]:
    return await DeveloperCredentialService(session, auth).list_credentials(
        developer_org_id
    )


@router.post(
    "/credentials/{credential_id}/rotate",
    response_model=CredentialCreateResult,
    dependencies=_CSRF,
)
async def rotate_credential(
    credential_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> CredentialCreateResult:
    result = await DeveloperCredentialService(session, auth).rotate(
        user, credential_id
    )
    await session.commit()
    return result


@router.delete(
    "/credentials/{credential_id}",
    response_model=CredentialRead,
    dependencies=_CSRF,
)
async def revoke_credential(
    credential_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
) -> CredentialRead:
    result = await DeveloperCredentialService(session, auth).revoke(
        user, credential_id
    )
    await session.commit()
    return result
