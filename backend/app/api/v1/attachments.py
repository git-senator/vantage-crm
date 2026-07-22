"""Attachment endpoints — the real upload workflow.

The client's sequence is three calls and one direct transfer:

    POST /attachments                 -> row + presigned PUT
    PUT  <upload.url>                 -> straight to object storage, not here
    POST /attachments/{id}/finalize   -> verified, published

`GET /attachments/{id}/download-url` mints a short-lived GET for an `available`
file. It is a GET on purpose — it is a read, it is idempotent, and a client may
need it again the moment the previous URL expires.

Note what no endpoint does: accept file bytes. The API handles metadata and
signatures only, so no request to this service is ever the size of an upload.
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
from app.models.attachment import Attachment
from app.schemas.attachment import (
    AttachmentCreate,
    AttachmentFinalize,
    AttachmentRead,
    AttachmentRegistered,
    PresignedDownload,
    PresignedUpload,
)
from app.services.attachment import AttachmentService

router = APIRouter()


def _to_read(attachment: Attachment) -> AttachmentRead:
    return AttachmentRead(
        id=attachment.id,
        entity_type=attachment.entity_type,
        entity_id=attachment.entity_id,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        checksum_sha256=attachment.checksum_sha256,
        status=attachment.status,
        scan_status=attachment.scan_status,
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
        upload_expires_at=attachment.upload_expires_at,
        available_at=attachment.available_at,
        failure_reason=attachment.failure_reason,
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
    attachment list, because an attachment only means something in context.

    No download URLs here: signing forty of them so a user can click one is
    forty bearer credentials issued for nothing.
    """
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
    return _to_read(
        await AttachmentService(session, auth).get_attachment(attachment_id)
    )


@router.post(
    "",
    response_model=AttachmentRegistered,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def register_attachment(
    payload: AttachmentCreate,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("documents.manage"))],
    user: CurrentUser,
) -> AttachmentRegistered:
    """Register a file and receive the credential to upload it.

    The response carries a presigned PUT the client must use promptly — it is
    short-lived, scoped to one key, and the only way bytes get into the bucket.
    The attachment is not usable until `finalize` verifies what arrived.
    """
    attachment, upload = await AttachmentService(session, auth).register(payload, user)
    response.headers["Location"] = f"/api/v1/attachments/{attachment.id}"
    return AttachmentRegistered(attachment=_to_read(attachment), upload=upload)


@router.post(
    "/{attachment_id}/upload-url",
    response_model=PresignedUpload,
    dependencies=[Depends(verify_csrf)],
)
async def request_upload_url(
    attachment_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("documents.manage"))],
    _user: CurrentUser,
) -> PresignedUpload:
    """Re-issue the upload credential for a registration still awaiting bytes.

    409 once the file has been uploaded: re-signing a PUT for a published object
    would hand out a credential to overwrite a verified, audited file.
    """
    return await AttachmentService(session, auth).presign_upload(attachment_id)


@router.post(
    "/{attachment_id}/finalize",
    response_model=AttachmentRead,
    dependencies=[Depends(verify_csrf)],
)
async def finalize_attachment(
    attachment_id: UUID,
    payload: AttachmentFinalize,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("documents.manage"))],
    user: CurrentUser,
) -> AttachmentRead:
    """Verify the uploaded object and publish it.

    Reads the bytes back from storage to establish the real size, hash and type
    — nothing here is taken from the client except an optional checksum, which
    is compared rather than trusted. A file that fails verification is deleted
    from storage and the row is marked `failed` with a reason the UI shows.
    """
    attachment = await AttachmentService(session, auth).finalize(
        attachment_id, user, claimed_checksum=payload.checksum_sha256
    )
    return _to_read(attachment)


@router.get("/{attachment_id}/download-url", response_model=PresignedDownload)
async def request_download_url(
    attachment_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("documents.view"))],
    user: CurrentUser,
) -> PresignedDownload:
    """A short-lived download URL for an `available` file.

    Issuing the URL *is* the access grant — the fetch itself never reaches this
    application — so this is the moment that gets audited.
    """
    return await AttachmentService(session, auth).presign_download(attachment_id, user)


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
    """Soft delete the metadata and remove the object from storage."""
    await AttachmentService(session, auth).delete_attachment(attachment_id, user)
