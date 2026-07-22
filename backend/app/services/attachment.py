"""Attachment business logic — metadata here, bytes in object storage.

The upload is a **three-step handshake**, and the shape is not incidental:

    1. register   →  the server creates the row, chooses the key, and returns a
                     presigned PUT scoped to that one key
    2. the client PUTs the bytes straight to storage — they never pass through
       this process
    3. finalize   →  the server reads the object back, verifies it, and only
                     then publishes it as `available`

**Why not just accept a multipart POST?** Because every byte would traverse the
API: a 25 MB upload on a slow connection occupies a worker for the whole
transfer, and memory scales with concurrent uploads rather than with request
count. Presigning moves the transfer to the storage layer, which is built for it.

**Why step 3 exists at all.** After step 2 the server knows nothing — the client
could have uploaded nothing, something enormous, or an executable named
`offer.pdf`. Finalization is the only point at which the file's real size, real
hash and real type are established, all read back *from storage*, and it is the
only path that can set `available`. A row that skips it stays unservable.

**Ordering matters in `finalize`.** Verification happens before the row is
published, and a rejected object is deleted from storage before the error is
raised — a file that failed the type check must not survive in the bucket where
a later bug, or a manual key lookup, could serve it.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    ServiceUnavailableError,
    UnsupportedMediaTypeError,
)
from app.core.logging import get_logger
from app.models.attachment import Attachment
from app.models.user import User
from app.repositories.attachment import AttachmentRepository
from app.schemas.attachment import AttachmentCreate, PresignedDownload, PresignedUpload
from app.services.audit import AuditService
from app.services.entity_access import EntityAccess
from app.services.rbac import AuthorizationContext
from app.services.storage import (
    ObjectNotFoundError,
    ObjectStorage,
    StorageError,
    get_object_storage,
)
from app.services.storage.keys import build_storage_key, key_belongs_to_organization
from app.services.storage.validation import (
    SNIFF_BYTES,
    is_allowed_content_type,
    normalise_content_type,
    verify_content,
)

logger = get_logger(__name__)

ENTITY_TYPE = "attachment"

#: Read in 1 MiB slices when checksumming. Large enough that a 25 MB file is 25
#: round trips rather than 25 000, small enough that the process never holds a
#: whole upload in memory.
_CHECKSUM_CHUNK = 1 << 20


class AttachmentService:
    def __init__(
        self,
        session: AsyncSession,
        auth: AuthorizationContext,
        *,
        storage: ObjectStorage | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.auth = auth
        self.attachments = AttachmentRepository(session)
        self.audit = AuditService(session)
        self.access = EntityAccess(session, auth)
        # Injectable so a test can drive the whole lifecycle against the
        # in-memory adapter without patching a module global.
        self.storage = storage or get_object_storage()
        self.settings = settings or get_settings()

    # ------------------------------------------------------------- reading

    async def list_for_entity(
        self, *, entity_type: str, entity_id: UUID, limit: int = 100
    ) -> list[Attachment]:
        self.auth.require("documents.view")
        await self.access.assert_readable(entity_type, entity_id)
        return await self.attachments.list_for_entity(
            self.auth.organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
        )

    async def get_attachment(self, attachment_id: UUID) -> Attachment:
        self.auth.require("documents.view")
        attachment = await self.attachments.get(
            attachment_id, self.auth.organization_id
        )
        if attachment is None:
            raise NotFoundError("Attachment not found.")
        await self.access.assert_readable(
            attachment.entity_type, attachment.entity_id
        )
        return attachment

    # ------------------------------------------------------------- writing

    async def register(
        self, payload: AttachmentCreate, actor: User
    ) -> tuple[Attachment, PresignedUpload]:
        """Create the row, choose the key, and hand back a presigned PUT.

        The content-type allowlist is checked *here*, before an upload URL
        exists, so a rejected type costs the client one round trip instead of a
        full transfer. It is checked again at finalization against the actual
        bytes, because this check is only as good as the client's honesty.
        """
        self.auth.require("documents.manage")
        await self.access.assert_readable(payload.entity_type, payload.entity_id)

        content_type = normalise_content_type(payload.content_type)
        if not is_allowed_content_type(content_type):
            raise UnsupportedMediaTypeError(
                f"Files of type {content_type} are not accepted."
            )

        expires_at = datetime.now(UTC) + timedelta(
            seconds=self.settings.UPLOAD_WINDOW_SECONDS
        )

        attachment = Attachment(
            organization_id=self.auth.organization_id,
            uploaded_by=actor.id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            filename=payload.filename,
            content_type=content_type,
            status="pending_upload",
            scan_status="pending",
            storage_backend=self.storage.name,
            upload_expires_at=expires_at,
        )
        self.session.add(attachment)
        # Flush to obtain the id, which is a component of the key: the key must
        # be unique per attachment so two files of the same name on the same
        # record cannot overwrite each other.
        await self.session.flush()

        attachment.storage_key = build_storage_key(
            organization_id=self.auth.organization_id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            attachment_id=attachment.id,
            filename=payload.filename,
        )
        await self.session.flush()

        upload = self._presign_upload_for(attachment)

        await self.audit.record(
            action=AuditAction.RECORD_CREATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=attachment.id,
            metadata={
                "filename": payload.filename,
                "on": f"{payload.entity_type}:{payload.entity_id}",
                "status": attachment.status,
                "content_type": content_type,
            },
        )
        logger.info(
            "attachment_registered",
            extra={
                "attachment_id": str(attachment.id),
                "storage_backend": self.storage.name,
            },
        )
        return attachment, upload

    def _presign_upload_for(self, attachment: Attachment) -> PresignedUpload:
        """Mint the PUT credential. Assumes the caller has already been authorised."""
        key = self._verified_key(attachment)
        expires_in = self.settings.S3_PRESIGN_TTL_SECONDS
        try:
            presigned = self.storage.presign_put(
                key,
                content_type=attachment.content_type,
                expires_in=expires_in,
                max_bytes=self.settings.MAX_UPLOAD_BYTES,
            )
        except StorageError as exc:
            logger.error(
                "presign_upload_failed",
                extra={
                    "attachment_id": str(attachment.id),
                    "retryable": exc.retryable,
                },
            )
            raise ServiceUnavailableError(
                "File storage is not available right now."
            ) from exc

        # The row's window is the shorter of the two: the signature dies at
        # `presigned.expires_at` whatever the column says, and a column that
        # promised longer would have the UI counting down to a URL that is
        # already dead.
        if (
            attachment.upload_expires_at is None
            or presigned.expires_at < attachment.upload_expires_at
        ):
            attachment.upload_expires_at = presigned.expires_at

        return PresignedUpload(
            url=presigned.url,
            expires_at=presigned.expires_at,
            required_headers=presigned.required_headers,
            max_bytes=self.settings.MAX_UPLOAD_BYTES,
        )

    def _verified_key(self, attachment: Attachment) -> str:
        """The row's storage key, re-checked against the caller's tenant.

        RLS already proved the row belongs to this organization. This proves the
        *key* does too, which is the thing actually being signed — a key edited
        out of band must not become a signed URL into another tenant's prefix.
        """
        key = attachment.storage_key
        if key is None:
            raise ConflictError("This attachment has no storage location.")
        if not key_belongs_to_organization(key, self.auth.organization_id):
            logger.error(
                "attachment_key_tenant_mismatch",
                extra={"attachment_id": str(attachment.id)},
            )
            raise NotFoundError("Attachment not found.")
        return key

    async def presign_upload(self, attachment_id: UUID) -> PresignedUpload:
        """Re-issue an upload URL for a registration that has not completed.

        Exists because the first URL is short-lived by design: a user who picks
        a file, gets distracted, and comes back should be able to retry without
        creating a second row and a second orphaned key.
        """
        self.auth.require("documents.manage")
        attachment = await self.get_attachment(attachment_id)

        if attachment.status != "pending_upload":
            raise ConflictError(
                "This file has already been uploaded."
                if attachment.status == "available"
                else "This file is not awaiting an upload."
            )
        if attachment.size_bytes is not None:
            # Bytes are in place and verified; it is the scan that is pending.
            raise ConflictError("This file has already been uploaded.")

        attachment.upload_expires_at = datetime.now(UTC) + timedelta(
            seconds=self.settings.UPLOAD_WINDOW_SECONDS
        )
        upload = self._presign_upload_for(attachment)
        await self.session.flush()
        return upload

    async def finalize(
        self,
        attachment_id: UUID,
        actor: User,
        *,
        claimed_checksum: str | None = None,
    ) -> Attachment:
        """Verify what was actually stored, then publish it.

        Idempotent for an already-published row: a client that retries after a
        dropped response gets the same attachment back rather than a 409, which
        is the difference between a resilient upload and one that fails on a
        flaky connection.
        """
        self.auth.require("documents.manage")
        attachment = await self.get_attachment(attachment_id)

        if attachment.status == "available":
            return attachment
        if attachment.status in ("failed", "quarantined"):
            raise ConflictError(
                attachment.failure_reason or "This upload was rejected."
            )
        if attachment.size_bytes is not None:
            # Verified already; waiting on the scanner. Not an error.
            return attachment

        key = self._verified_key(attachment)

        try:
            stored = await self.storage.head(key)
        except ObjectNotFoundError as exc:
            # Nothing was uploaded. The row stays claimable — the client may
            # simply have failed mid-PUT and be about to retry.
            raise ConflictError(
                "No file has been uploaded for this attachment yet."
            ) from exc
        except StorageError as exc:
            raise ServiceUnavailableError(
                "File storage is not available right now."
            ) from exc

        if stored.size_bytes == 0:
            await self._reject(attachment, actor, "The uploaded file is empty.")
            raise UnsupportedMediaTypeError("The uploaded file is empty.")

        if stored.size_bytes > self.settings.MAX_UPLOAD_BYTES:
            limit_mb = self.settings.MAX_UPLOAD_BYTES // (1024 * 1024)
            reason = f"The file exceeds the {limit_mb} MB limit."
            await self._reject(attachment, actor, reason)
            raise PayloadTooLargeError(reason)

        head_bytes = await self.storage.read(key, max_bytes=SNIFF_BYTES)
        verdict = verify_content(attachment.content_type, head_bytes)
        if not verdict.ok:
            reason = verdict.reason or "The file could not be verified."
            await self._reject(
                attachment, actor, reason, detected=verdict.content_type
            )
            raise UnsupportedMediaTypeError(reason)

        checksum = await self._checksum(key)
        if claimed_checksum is not None and claimed_checksum != checksum:
            reason = "The uploaded file does not match the checksum sent with it."
            await self._reject(attachment, actor, reason)
            raise ConflictError(reason)

        attachment.size_bytes = stored.size_bytes
        attachment.checksum_sha256 = checksum
        attachment.content_type = verdict.content_type
        attachment.upload_expires_at = None

        if self.settings.MALWARE_SCAN_ENABLED:
            # Held back deliberately: between "the bytes are the type they
            # claim" and "the bytes are not malware" there is a window, and a
            # file must not be servable inside it. The scan job publishes it.
            attachment.scan_status = "pending"
        else:
            attachment.scan_status = "skipped"
            attachment.status = "available"
            attachment.available_at = datetime.now(UTC)

        await self.session.flush()

        await self.audit.record(
            action=AuditAction.DOCUMENT_UPLOADED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=attachment.id,
            metadata={
                "filename": attachment.filename,
                "on": f"{attachment.entity_type}:{attachment.entity_id}",
                "size_bytes": stored.size_bytes,
                "content_type": attachment.content_type,
                "checksum_sha256": checksum,
                "status": attachment.status,
            },
        )
        logger.info(
            "attachment_finalized",
            extra={
                "attachment_id": str(attachment.id),
                "size_bytes": stored.size_bytes,
                "status": attachment.status,
            },
        )
        return attachment

    async def _checksum(self, key: str) -> str:
        digest = hashlib.sha256()
        async for chunk in self.storage.stream(key, chunk_size=_CHECKSUM_CHUNK):
            digest.update(chunk)
        return digest.hexdigest()

    async def _reject(
        self,
        attachment: Attachment,
        actor: User,
        reason: str,
        *,
        detected: str | None = None,
    ) -> None:
        """Mark the row failed and remove the bytes.

        The delete is best-effort: if storage refuses, the row is still marked
        `failed` — an unservable row with a stray object is recoverable by the
        sweeper, whereas a servable row whose bytes failed verification is not
        recoverable at all.
        """
        if attachment.storage_key:
            try:
                await self.storage.delete(attachment.storage_key)
            except StorageError:
                logger.exception(
                    "rejected_object_delete_failed",
                    extra={"attachment_id": str(attachment.id)},
                )

        attachment.status = "failed"
        attachment.failure_reason = reason[:500]
        attachment.upload_expires_at = None
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.DOCUMENT_REJECTED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=attachment.id,
            metadata={
                "filename": attachment.filename,
                "reason": reason,
                "declared_content_type": attachment.content_type,
                "detected_content_type": detected,
            },
        )
        logger.warning(
            "attachment_rejected",
            extra={"attachment_id": str(attachment.id), "reason": reason},
        )

    # ----------------------------------------------------------- downloads

    async def presign_download(
        self, attachment_id: UUID, actor: User
    ) -> PresignedDownload:
        """Mint a short-lived GET for an `available` file.

        `documents.view` plus readability of the parent record — the same gate
        as seeing that the file exists at all. Nothing else is servable: a row
        that is pending, failed or quarantined has no download path, and that is
        checked here rather than trusted to the caller.
        """
        self.auth.require("documents.view")
        attachment = await self.get_attachment(attachment_id)

        if attachment.status != "available":
            raise ConflictError(
                attachment.failure_reason
                or "This file is not available for download yet."
            )
        if attachment.scan_status == "infected":  # pragma: no cover — defensive
            # Unreachable while quarantine also sets `status`, and deliberately
            # kept: this is the check that must not depend on another one.
            raise ConflictError("This file was quarantined and cannot be served.")

        key = self._verified_key(attachment)
        try:
            presigned = self.storage.presign_get(
                key,
                expires_in=self.settings.S3_DOWNLOAD_TTL_SECONDS,
                filename=attachment.filename,
                content_type=attachment.content_type,
            )
        except StorageError as exc:
            raise ServiceUnavailableError(
                "File storage is not available right now."
            ) from exc

        await self.audit.record(
            action=AuditAction.DOCUMENT_DOWNLOADED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=attachment.id,
            metadata={
                "filename": attachment.filename,
                "on": f"{attachment.entity_type}:{attachment.entity_id}",
            },
        )
        return PresignedDownload(
            url=presigned.url,
            expires_at=presigned.expires_at,
            filename=attachment.filename,
        )

    # ------------------------------------------------------------ deletion

    async def delete_attachment(self, attachment_id: UUID, actor: User) -> None:
        """Soft delete the metadata and remove the bytes.

        The row survives — soft delete is what makes an audit trail and a
        retention policy possible — but the object does not. Keeping bytes
        behind a "deleted" row is how a deletion request quietly fails to be one.

        Storage failure does not block the delete. The row is marked deleted
        with its key intact and the sweeper reclaims the object later; the
        alternative is a user who cannot remove a file because a bucket is
        having a bad minute.
        """
        self.auth.require("documents.manage")
        attachment = await self.get_attachment(attachment_id)

        if attachment.storage_key:
            try:
                await self.storage.delete(attachment.storage_key)
            except StorageError:
                logger.exception(
                    "attachment_object_delete_failed",
                    extra={"attachment_id": str(attachment.id)},
                )

        attachment.deleted_at = datetime.now(UTC)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.RECORD_DELETED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=attachment.id,
            metadata={"filename": attachment.filename},
        )
