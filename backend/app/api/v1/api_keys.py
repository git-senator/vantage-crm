"""API key management endpoints.

All gated on `settings.manage` (inside the service) — issuing a machine
credential that carries a slice of an admin's authority is an administrative
act. The raw secret is returned only by create and rotate, and never again.

This is the *management* surface, used by a signed-in user. The machine
authentication path — where a key is presented as a credential on a request —
is `MachineAuth` in `app.api.v1.dependencies`, for the Phase 7.2 public API.
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
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyRead, to_created, to_read
from app.services.api_key import ApiKeyService

router = APIRouter()


@router.get("", response_model=list[ApiKeyRead])
async def list_api_keys(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
    settings: SettingsDep,
) -> list[ApiKeyRead]:
    """Every key in the workspace, newest first. Never includes a secret."""
    keys = await ApiKeyService(session, settings).list_keys(auth)
    return [to_read(key) for key in keys]


@router.post(
    "",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_api_key(
    payload: ApiKeyCreate,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> ApiKeyCreated:
    """Mint a key. The response carries the raw secret **once** — store it now."""
    key, secret = await ApiKeyService(session, settings).create(
        auth,
        user,
        name=payload.name,
        scopes=dict(payload.scopes),
        expires_in_days=payload.expires_in_days,
    )
    await session.commit()
    return to_created(key, secret)


@router.post(
    "/{key_id}/rotate",
    response_model=ApiKeyCreated,
    dependencies=[Depends(verify_csrf)],
)
async def rotate_api_key(
    key_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> ApiKeyCreated:
    """Issue a fresh secret, invalidating the old one. Same scopes and expiry."""
    key, secret = await ApiKeyService(session, settings).rotate(auth, user, key_id)
    await session.commit()
    return to_created(key, secret)


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def revoke_api_key(
    key_id: UUID,
    session: TenantSessionDep,
    auth: Authorization,
    user: CurrentUser,
    settings: SettingsDep,
) -> None:
    """Revoke a key. Idempotent."""
    await ApiKeyService(session, settings).revoke(auth, user, key_id)
    await session.commit()
