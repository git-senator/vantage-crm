"""Attachment business logic — the placeholder seam for Phase 3 storage.

`register` creates the metadata row and computes the object key the file *will*
occupy, then returns. It does **not** touch object storage, because there is
none yet. The method is deliberately named `register`, not `upload`: the upload
is a separate step that does not exist, and calling this one does not move any
bytes.

`presign_upload` / `presign_download` are stubs that raise `NotImplementedError`
with a message pointing at Phase 3. They exist so the call sites the frontend
and the API layer need are already named and typed — wiring MinIO/S3 later is
filling in two method bodies, not threading a new concept through the service.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.models.attachment import Attachment
from app.models.user import User
from app.repositories.attachment import AttachmentRepository
from app.schemas.attachment import AttachmentCreate
from app.services.audit import AuditService
from app.services.entity_access import EntityAccess
from app.services.rbac import AuthorizationContext

logger = get_logger(__name__)

ENTITY_TYPE = "attachment"


class AttachmentService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.attachments = AttachmentRepository(session)
        self.audit = AuditService(session)
        self.access = EntityAccess(session, auth)

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

    async def register(self, payload: AttachmentCreate, actor: User) -> Attachment:
        """Record that a file belongs to a record. Stores no bytes.

        The row lands in `pending_upload`. In Phase 3, the client would then
        `presign_upload`, PUT the file, and the post-upload pipeline would flip
        it to `available`. Today it simply waits — which is the correct, honest
        state for a file whose storage layer has not shipped.
        """
        self.auth.require("documents.manage")
        await self.access.assert_readable(payload.entity_type, payload.entity_id)

        attachment = Attachment(
            organization_id=self.auth.organization_id,
            uploaded_by=actor.id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            filename=payload.filename,
            content_type=payload.content_type,
            status="pending_upload",
        )
        self.session.add(attachment)
        await self.session.flush()

        # A deterministic candidate key, so the eventual upload has a target.
        # No object exists behind it. Partitioned by org and entity to keep the
        # future bucket navigable and to make per-tenant lifecycle rules easy.
        attachment.storage_key = (
            f"org/{self.auth.organization_id}/"
            f"{payload.entity_type}/{payload.entity_id}/{attachment.id}/"
            f"{payload.filename}"
        )
        await self.session.flush()

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
            },
        )
        logger.info(
            "attachment_registered",
            extra={"attachment_id": str(attachment.id), "storage": "pending:phase-3"},
        )
        return attachment

    async def delete_attachment(self, attachment_id: UUID, actor: User) -> None:
        """Soft delete the metadata. There are no bytes to remove yet."""
        self.auth.require("documents.manage")
        attachment = await self.get_attachment(attachment_id)

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

    # -------------------------------------------------- Phase 3 storage seam

    async def presign_upload(self, attachment_id: UUID) -> str:  # pragma: no cover
        raise NotImplementedError(
            "Presigned uploads arrive in Phase 3 with the object-storage layer."
        )

    async def presign_download(self, attachment_id: UUID) -> str:  # pragma: no cover
        raise NotImplementedError(
            "Presigned downloads arrive in Phase 3 with the object-storage layer."
        )
