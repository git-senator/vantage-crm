"""Attachments — file metadata placeholders, no bytes.

Phase 2.8 ships the architecture, not the storage. What these tests pin down is
exactly that boundary: a registered attachment is a real, tenant-scoped,
permission-checked, audited row that carries *no content* and openly says so via
its `pending_upload` status. The upload/download path raises `NotImplementedError`
pointing at Phase 3, so it cannot be mistaken for working.

Visibility and writes are gated through the same `EntityAccess` resolver as
notes, on the `documents.view` / `documents.manage` permissions.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.attachment import Attachment
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.schemas.attachment import AttachmentCreate
from app.schemas.lead import LeadCreate
from app.services.attachment import AttachmentService
from app.services.lead import LeadService
from app.services.rbac import AuthorizationContext
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


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


class TestRegistration:
    async def test_register_creates_a_pending_row_with_no_bytes(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        att = await AttachmentService(db, auth).register(_create(lead_record.id), user)
        assert att.status == "pending_upload"
        assert att.size_bytes is None
        assert att.checksum_sha256 is None
        # A storage target is computed, but nothing lives behind it.
        assert att.storage_key is not None
        assert str(lead_record.id) in att.storage_key
        assert att.uploaded_by == user.id

    async def test_register_audits(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await AttachmentService(db, auth).register(_create(lead_record.id), user)
        entries = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_CREATED)
            )
        ).scalars().all()
        assert any(e.entity_type == "attachment" for e in entries)

    async def test_cannot_attach_to_a_record_you_cannot_see(
        self, db: AsyncSession, organization: Organization, rbac_seeded, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        with pytest.raises(NotFoundError):
            await AttachmentService(db, agent_auth).register(
                _create(lead_record.id), agent
            )

    async def test_requires_documents_manage(
        self, db: AsyncSession, organization: Organization, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        viewer = await make_user(db, organization, "viewer@vantage.example")
        viewer_auth = AuthorizationContext(
            user_id=viewer.id,
            organization_id=organization.id,
            role_keys=("viewer",),
            grants={"leads.view": Scope.ALL, "documents.view": Scope.ALL},
        )
        with pytest.raises(PermissionDeniedError):
            await AttachmentService(db, viewer_auth).register(
                _create(lead_record.id), viewer
            )


class TestReadAndDelete:
    async def test_list_follows_the_parent(
        self, db: AsyncSession, admin, organization: Organization, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await AttachmentService(db, auth).register(_create(lead_record.id), user)

        agent = await make_user(db, organization, "agent@vantage.example")
        agent_auth = await auth_for(db, agent, "agent")
        with pytest.raises(NotFoundError):
            await AttachmentService(db, agent_auth).list_for_entity(
                entity_type="lead", entity_id=lead_record.id
            )

    async def test_owner_can_list_its_attachments(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await AttachmentService(db, auth).register(_create(lead_record.id), user)
        rows = await AttachmentService(db, auth).list_for_entity(
            entity_type="lead", entity_id=lead_record.id
        )
        assert len(rows) == 1

    async def test_delete_is_soft_and_audits(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = AttachmentService(db, auth)
        att = await service.register(_create(lead_record.id), user)
        await service.delete_attachment(att.id, user)

        with pytest.raises(NotFoundError):
            await service.get_attachment(att.id)

        row = (
            await db.execute(select(Attachment).where(Attachment.id == att.id))
        ).unique().scalar_one()
        assert row.deleted_at is not None

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == AuditAction.RECORD_DELETED)
            )
        ).scalar_one()
        assert entry.entity_type == "attachment"


class TestStorageSeamIsNotWired:
    async def test_presign_upload_is_not_implemented(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        """The seam exists and is named; it must not pretend to work."""
        user, auth = admin
        att = await AttachmentService(db, auth).register(_create(lead_record.id), user)
        with pytest.raises(NotImplementedError, match="Phase 3"):
            await AttachmentService(db, auth).presign_upload(att.id)

    async def test_presign_download_is_not_implemented(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        att = await AttachmentService(db, auth).register(_create(lead_record.id), user)
        with pytest.raises(NotImplementedError, match="Phase 3"):
            await AttachmentService(db, auth).presign_download(att.id)


class TestTenantIsolation:
    async def test_rls_blocks_an_unscoped_query(
        self, db: AsyncSession, admin, lead_record
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await AttachmentService(db, auth).register(_create(lead_record.id), user)
        await db.commit()

        from app.db.sql_objects import tenant_policy_statements

        for statement in tenant_policy_statements("attachments"):
            await db.execute(text(statement))
        await db.commit()

        try:
            async with db.begin():
                rows = (
                    await db.execute(select(Attachment))
                ).unique().scalars().all()
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
