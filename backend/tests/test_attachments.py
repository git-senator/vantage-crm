"""Attachments — the real storage lifecycle.

Phase 2.8 shipped metadata with no bytes, and these tests pinned that boundary
down. Phase 3.1 replaced the placeholder with object storage, so what they pin
down now is the boundary that actually matters: **a file is servable only after
the server has read it back and verified it**, and every way around that is
closed.

The cases worth reading first are `TestVerification` — an executable renamed to
`.pdf`, an oversized file, a checksum that does not match — and
`TestAccessControl`, which proves a signed URL cannot be obtained for a record
the caller cannot read.

Storage is the in-memory adapter, which issues genuine signed URLs with real
expiry. Nothing here needs MinIO; the S3 adapter is the same code path, and the
compose stack exercises it.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    UnsupportedMediaTypeError,
)
from app.core.permissions import Scope
from app.models.attachment import Attachment
from app.models.audit import AuditLog
from app.schemas.attachment import AttachmentCreate
from app.schemas.lead import LeadCreate
from app.schemas.note import NoteCreate
from app.services.attachment import AttachmentService
from app.services.lead import LeadService
from app.services.note import NoteService
from app.services.rbac import AuthorizationContext
from app.services.storage.memory import InMemoryObjectStorage, SignatureError
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n1 0 obj\n<</Type/Catalog>>\nendobj\n"
EXECUTABLE = b"MZ\x90\x00\x03" + b"\x00" * 64


@pytest.fixture
async def lead_record(db: AsyncSession, admin):  # type: ignore[no-untyped-def]
    user, auth = admin
    return await LeadService(db, auth).create_lead(
        LeadCreate(first_name="Sana", last_name="Kaur"), user
    )


def _create(entity_id, **overrides: object) -> AttachmentCreate:  # type: ignore[no-untyped-def]
    data: dict = {
        "entity_type": "lead",
        "entity_id": entity_id,
        "filename": "pre-approval.pdf",
        "content_type": "application/pdf",
        **overrides,
    }
    return AttachmentCreate(**data)


def _service(
    db: AsyncSession,
    auth: AuthorizationContext,
    storage: InMemoryObjectStorage,
    settings: Settings,
) -> AttachmentService:
    return AttachmentService(db, auth, storage=storage, settings=settings)


async def _upload(
    service: AttachmentService,
    storage: InMemoryObjectStorage,
    attachment: Attachment,
    payload: bytes,
    *,
    content_type: str = "application/pdf",
) -> None:
    """Stand in for the browser's PUT to the presigned URL.

    Deliberately goes through `resolve` rather than writing the key directly:
    the write is only legitimate if the URL the service handed out actually
    authorises it, which is the property under test everywhere else.
    """
    upload = service._presign_upload_for(attachment)
    key = storage.resolve(upload.url, "put")
    await storage.write(key, payload, content_type=content_type)


class TestRegistration:
    async def test_register_returns_a_pending_row_and_an_upload_url(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        attachment, upload = await _service(
            db, auth, object_storage, storage_settings
        ).register(_create(lead_record.id), user)

        assert attachment.status == "pending_upload"
        # Nothing is known about the bytes yet, because there are none.
        assert attachment.size_bytes is None
        assert attachment.checksum_sha256 is None
        assert attachment.storage_key is not None
        assert attachment.storage_key.startswith(f"org/{auth.organization_id}/")
        assert attachment.upload_expires_at is not None

        assert upload.url
        assert upload.required_headers["Content-Type"] == "application/pdf"
        assert upload.max_bytes == storage_settings.MAX_UPLOAD_BYTES
        # The URL authorises this attachment's key and nothing else.
        assert object_storage.resolve(upload.url, "put") == attachment.storage_key

    async def test_a_rejected_type_never_gets_an_upload_url(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The allowlist is checked before signing, so a client cannot spend a
        transfer discovering the answer — and cannot write the object at all."""
        user, auth = admin
        with pytest.raises(UnsupportedMediaTypeError):
            await _service(db, auth, object_storage, storage_settings).register(
                _create(lead_record.id, content_type="application/x-msdownload"),
                user,
            )
        assert object_storage.keys() == []

    async def test_register_audits(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await _service(db, auth, object_storage, storage_settings).register(
            _create(lead_record.id), user
        )
        entries = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.action == AuditAction.RECORD_CREATED
                    )
                )
            )
            .scalars()
            .all()
        )
        assert any(e.entity_type == "attachment" for e in entries)

    async def test_cannot_attach_to_a_record_you_cannot_see(
        self,
        db,
        organization,
        rbac_seeded,
        lead_record,
        object_storage,
        storage_settings,
    ) -> None:  # type: ignore[no-untyped-def]
        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        with pytest.raises(NotFoundError):
            await _service(db, agent_auth, object_storage, storage_settings).register(
                _create(lead_record.id), agent
            )

    async def test_requires_documents_manage(
        self, db, organization, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        viewer = await make_user(db, organization, "viewer@vantage.example")
        viewer_auth = AuthorizationContext(
            user_id=viewer.id,
            organization_id=organization.id,
            role_keys=("viewer",),
            grants={"leads.view": Scope.ALL, "documents.view": Scope.ALL},
        )
        with pytest.raises(PermissionDeniedError):
            await _service(db, viewer_auth, object_storage, storage_settings).register(
                _create(lead_record.id), viewer
            )


class TestUploadAndFinalize:
    async def test_the_happy_path_publishes_a_verified_file(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)

        await _upload(service, object_storage, attachment, PDF)
        finalized = await service.finalize(attachment.id, user)

        assert finalized.status == "available"
        assert finalized.available_at is not None
        # Size and hash are read back from storage, never taken from a client.
        assert finalized.size_bytes == len(PDF)
        assert finalized.checksum_sha256 == hashlib.sha256(PDF).hexdigest()
        # The upload window is closed: the row is no longer claimable.
        assert finalized.upload_expires_at is None
        assert finalized.scan_status == "skipped"

    async def test_finalize_before_any_upload_is_a_conflict_not_a_failure(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """A client that failed mid-PUT should be able to retry, so the row must
        stay claimable rather than being burned."""
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)

        with pytest.raises(ConflictError, match="No file has been uploaded"):
            await service.finalize(attachment.id, user)

        assert attachment.status == "pending_upload"
        assert attachment.upload_expires_at is not None

    async def test_finalize_is_idempotent(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """A retried finalize after a dropped response must not 409 — that is
        the difference between a resilient upload and one that fails on a flaky
        connection."""
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, PDF)

        first = await service.finalize(attachment.id, user)
        second = await service.finalize(attachment.id, user)
        assert first.id == second.id
        assert second.status == "available"

    async def test_upload_url_can_be_reissued_while_pending(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)

        reissued = await service.presign_upload(attachment.id)
        assert object_storage.resolve(reissued.url, "put") == attachment.storage_key

    async def test_upload_url_is_refused_once_the_file_is_published(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """Re-signing a PUT for a published object would hand out a credential
        to overwrite a verified, audited file."""
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, PDF)
        await service.finalize(attachment.id, user)

        with pytest.raises(ConflictError, match="already been uploaded"):
            await service.presign_upload(attachment.id)

    async def test_upload_audits_separately_from_registration(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, PDF)
        await service.finalize(attachment.id, user)

        entry = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.DOCUMENT_UPLOADED
                )
            )
        ).scalar_one()
        assert entry.entity_type == "attachment"
        assert entry.metadata_["size_bytes"] == len(PDF)


class TestVerification:
    async def test_an_executable_renamed_to_pdf_is_rejected_and_deleted(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The declared type is a hint from an untrusted party; the bytes are
        evidence. The object must not survive in the bucket."""
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, EXECUTABLE)

        with pytest.raises(UnsupportedMediaTypeError):
            await service.finalize(attachment.id, user)

        assert attachment.status == "failed"
        assert attachment.failure_reason
        assert object_storage.keys() == []

    async def test_an_oversized_file_is_rejected_and_deleted(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        oversized = PDF + b"\x00" * (storage_settings.MAX_UPLOAD_BYTES + 1)
        await _upload(service, object_storage, attachment, oversized)

        with pytest.raises(PayloadTooLargeError):
            await service.finalize(attachment.id, user)

        assert attachment.status == "failed"
        assert object_storage.keys() == []

    async def test_an_empty_upload_is_rejected(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, b"")

        with pytest.raises(UnsupportedMediaTypeError, match="empty"):
            await service.finalize(attachment.id, user)

    async def test_a_checksum_mismatch_rejects_the_upload(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The client's claim is compared against what was stored, never trusted
        in place of it."""
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, PDF)

        with pytest.raises(ConflictError, match="checksum"):
            await service.finalize(attachment.id, user, claimed_checksum="0" * 64)
        assert attachment.status == "failed"

    async def test_a_matching_checksum_passes(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, PDF)

        finalized = await service.finalize(
            attachment.id, user, claimed_checksum=hashlib.sha256(PDF).hexdigest()
        )
        assert finalized.status == "available"

    async def test_a_rejected_upload_audits_with_its_reason(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, EXECUTABLE)
        with pytest.raises(UnsupportedMediaTypeError):
            await service.finalize(attachment.id, user)

        entry = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.DOCUMENT_REJECTED
                )
            )
        ).scalar_one()
        assert entry.metadata_["reason"]

    async def test_a_failed_row_cannot_be_retried_into_availability(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """Once rejected, the row is terminal: re-uploading good bytes under the
        same id must not launder a file that already failed verification."""
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, EXECUTABLE)
        with pytest.raises(UnsupportedMediaTypeError):
            await service.finalize(attachment.id, user)

        with pytest.raises(ConflictError):
            await service.presign_upload(attachment.id)
        with pytest.raises(ConflictError):
            await service.finalize(attachment.id, user)


class TestDownload:
    async def test_an_available_file_yields_a_signed_url(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, PDF)
        await service.finalize(attachment.id, user)

        download = await service.presign_download(attachment.id, user)
        assert download.filename == "pre-approval.pdf"
        assert object_storage.resolve(download.url, "get") == attachment.storage_key
        # A download URL must not double as a write credential.
        with pytest.raises(SignatureError):
            object_storage.resolve(download.url, "put")

    async def test_a_pending_file_has_no_download(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        with pytest.raises(ConflictError):
            await service.presign_download(attachment.id, user)

    async def test_a_failed_file_has_no_download(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, EXECUTABLE)
        with pytest.raises(UnsupportedMediaTypeError):
            await service.finalize(attachment.id, user)

        with pytest.raises(ConflictError):
            await service.presign_download(attachment.id, user)

    async def test_issuing_a_download_url_is_audited(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """Issuing the URL *is* the access grant — the fetch never reaches this
        application, so there is no later moment to record."""
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, PDF)
        await service.finalize(attachment.id, user)
        await service.presign_download(attachment.id, user)

        entry = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.DOCUMENT_DOWNLOADED
                )
            )
        ).scalar_one()
        assert entry.entity_type == "attachment"


class TestAccessControl:
    async def test_another_agent_cannot_obtain_a_download_url(
        self, db, admin, organization, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The exit criterion in prose: a presigned URL is not reusable across
        users, because a user who cannot read the record never gets one."""
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, PDF)
        await service.finalize(attachment.id, user)

        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        with pytest.raises(NotFoundError):
            await _service(
                db, agent_auth, object_storage, storage_settings
            ).presign_download(attachment.id, agent)

    async def test_list_follows_the_parent(
        self, db, admin, organization, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await _service(db, auth, object_storage, storage_settings).register(
            _create(lead_record.id), user
        )

        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        with pytest.raises(NotFoundError):
            await _service(
                db, agent_auth, object_storage, storage_settings
            ).list_for_entity(entity_type="lead", entity_id=lead_record.id)

    async def test_owner_can_list_its_attachments(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        await service.register(_create(lead_record.id), user)
        rows = await service.list_for_entity(
            entity_type="lead", entity_id=lead_record.id
        )
        assert len(rows) == 1

    async def test_a_key_from_another_tenant_is_never_signed(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """Defence in depth behind RLS: even if a row's key were corrupted to
        point at another tenant's prefix, no URL is minted for it."""
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, PDF)
        await service.finalize(attachment.id, user)

        attachment.storage_key = "org/00000000-0000-0000-0000-000000000000/x/y/z/a.pdf"
        await db.flush()

        with pytest.raises(NotFoundError):
            await service.presign_download(attachment.id, user)


class TestNoteAttachments:
    async def test_a_note_can_carry_a_file(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        note = await NoteService(db, auth).create_note(
            NoteCreate(
                entity_type="lead", entity_id=lead_record.id, body="Financing terms"
            ),
            user,
        )
        attachment, _ = await _service(
            db, auth, object_storage, storage_settings
        ).register(_create(note.id, entity_type="note"), user)
        assert attachment.entity_type == "note"

    async def test_a_note_attachment_inherits_the_notes_parent_visibility(
        self, db, admin, organization, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """A note has no scope anchor of its own, so attaching to one must not
        become a way around the lead's scope."""
        user, auth = admin
        note = await NoteService(db, auth).create_note(
            NoteCreate(entity_type="lead", entity_id=lead_record.id, body="Private"),
            user,
        )
        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")

        with pytest.raises(NotFoundError):
            await _service(db, agent_auth, object_storage, storage_settings).register(
                _create(note.id, entity_type="note"), agent
            )


class TestDeletion:
    async def test_delete_is_soft_for_metadata_and_hard_for_bytes(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        """The row survives for the audit trail; the object does not. Keeping
        bytes behind a deleted row is how a deletion quietly fails to be one."""
        user, auth = admin
        service = _service(db, auth, object_storage, storage_settings)
        attachment, _ = await service.register(_create(lead_record.id), user)
        await _upload(service, object_storage, attachment, PDF)
        await service.finalize(attachment.id, user)
        assert object_storage.keys()

        await service.delete_attachment(attachment.id, user)

        assert object_storage.keys() == []
        with pytest.raises(NotFoundError):
            await service.get_attachment(attachment.id)

        row = (
            (
                await db.execute(
                    select(Attachment).where(Attachment.id == attachment.id)
                )
            )
            .unique()
            .scalar_one()
        )
        assert row.deleted_at is not None

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_DELETED)
            )
        ).scalar_one()
        assert entry.entity_type == "attachment"


class TestTenantIsolation:
    async def test_rls_blocks_an_unscoped_query(
        self, db, admin, lead_record, object_storage, storage_settings
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await _service(db, auth, object_storage, storage_settings).register(
            _create(lead_record.id), user
        )
        await db.commit()

        from app.db.sql_objects import tenant_policy_statements

        for statement in tenant_policy_statements("attachments"):
            await db.execute(text(statement))
        await db.commit()

        try:
            async with db.begin():
                rows = (await db.execute(select(Attachment))).unique().scalars().all()
            assert rows == [], (
                "An unscoped query returned rows with no tenant context bound. "
                "RLS is not enforcing on attachments."
            )
        finally:
            await db.execute(
                text("DROP POLICY IF EXISTS tenant_isolation ON attachments")
            )
            await db.execute(
                text("ALTER TABLE attachments NO FORCE ROW LEVEL SECURITY")
            )
            await db.execute(
                text("ALTER TABLE attachments DISABLE ROW LEVEL SECURITY")
            )
            await db.commit()
