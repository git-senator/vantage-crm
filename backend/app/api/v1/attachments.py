"""Attachment endpoints — Phase 2.8 placeholders.

Registration and metadata CRUD exist and are audited; the actual upload and
download live behind `presign_upload`/`presign_download`, which return 501 until
Phase 3 brings object storage. This is deliberate: the frontend can build the
attachments UI now against a real, permission-checked, tenant-scoped API, and
the only thing missing is the bytes.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    TenantSessionDep,
    require,
    verify_csrf,
)
from app.schemas.attachment import AttachmentCreate, AttachmentRead
from app.services.attachment import AttachmentService

router = APIRouter()


def _to_read(attachment) -> AttachmentRead:  # type: ignore[no-untyped-def]
    return AttachmentRead(
        id=attachment.id,
        entity_type=attachment.entity_type,
        entity_id=attachment.entity_id,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        status=attachment.status,
        storage_backend=attachment.storage_backend,
        uploader=(
            {
                "id": attachment.uploader.id,
                "full_name": attachment.uploader.full_name,
                "initials": attachment.uploader.initials,
                "avatar_hue": attachment.uploader.avatar_hue,
            }
            if attachment.uploader
            else None
        ),
        # Phase 3 fills these; None today, and `status` explains why.
        upload_url=None,
        download_url=None,
        created_at=attachment.created_at,
        updated_at=attachment.updated_at,
    )


@router.get("", response_model=list[AttachmentRead])
async def list_attachments(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("documents.view"))],
    _user: CurrentUser,
    entity_type: Annotated[str, Query()],
    entity_id: Annotated[UUID, Query()],
) -> list[AttachmentRead]:
    """Attachments on one record. The entity is required — there is no global
    attachment list, because an attachment only means something in context."""
    rows = await AttachmentService(session, auth).list_for_entity(
        entity_type=entity_type, entity_id=entity_id
    )
    return [_to_read(row) for row in rows]


@router.get("/{attachment_id}", response_model=AttachmentRead)
async def get_attachment(
    attachment_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("documents.view"))],
    _user: CurrentUser,
) -> AttachmentRead:
    return _to_read(await AttachmentService(session, auth).get_attachment(attachment_id))


@router.post(
    "",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def register_attachment(
    payload: AttachmentCreate,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("documents.manage"))],
    user: CurrentUser,
) -> AttachmentRead:
    """Register a file's metadata. Stores no bytes — the row waits in
    `pending_upload` for the Phase 3 upload path."""
    attachment = await AttachmentService(session, auth).register(payload, user)
    response.headers["Location"] = f"/api/v1/attachments/{attachment.id}"
    return _to_read(attachment)


@router.post(
    "/{attachment_id}/upload-url",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    dependencies=[Depends(verify_csrf)],
)
async def request_upload_url(
    attachment_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("documents.manage"))],
    _user: CurrentUser,
) -> dict[str, str]:
    """Phase 3 returns a presigned PUT here. Today it is a documented 501 so the
    frontend can wire the button and handle the not-yet-available response."""
    # Prove the caller could act on this attachment before disclosing anything.
    await AttachmentService(session, auth).get_attachment(attachment_id)
    return {
        "detail": "Uploads are not available until Phase 3 object storage ships.",
        "status": "pending_upload",
    }


@router.delete(
    "/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_attachment(
    attachment_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("documents.manage"))],
    user: CurrentUser,
) -> None:
    """Soft delete the metadata. No bytes to remove yet."""
    await AttachmentService(session, auth).delete_attachment(attachment_id, user)
