"""Attachment data access.

Like notes, an attachment has no scope anchor of its own — visibility follows
the record it hangs off, and the service proves readability of the parent
before listing. Ordering is newest-first within a record.

The two maintenance queries at the bottom back the background jobs. They are
still tenant-scoped — deliberately, because RLS is enabled on this table and a
query with no tenant context bound would return zero rows whatever it asked
for. The sweeper resolves the tenant list separately and then runs each
organization's work with its context bound, so no job path bypasses RLS. See
`app/workers/`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import joinedload

from app.models.attachment import Attachment
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE


class AttachmentRepository(BaseRepository[Attachment]):
    model = Attachment

    def _base(self, organization_id: UUID) -> Select[tuple[Attachment]]:
        return (
            self.scoped_to_organization(self._base_query(), organization_id)
            .options(joinedload(Attachment.uploader))
            .execution_options(populate_existing=True)
        )

    async def list_for_entity(
        self,
        organization_id: UUID,
        *,
        entity_type: str,
        entity_id: UUID,
        limit: int = 100,
    ) -> list[Attachment]:
        query = (
            self._base(organization_id)
            .where(Attachment.entity_type == entity_type)
            .where(Attachment.entity_id == entity_id)
            .order_by(Attachment.created_at.desc(), Attachment.id.desc())
            .limit(min(limit, MAX_PAGE_SIZE))
        )
        return list((await self.session.execute(query)).unique().scalars().all())

    async def list_for_organization(
        self,
        organization_id: UUID,
        *,
        search: str | None = None,
        status: str | None = None,
        entity_type: str | None = None,
        limit: int = 100,
    ) -> list[Attachment]:
        """The workspace document library: every attachment in the org, newest
        first, with optional filename/status/entity-type filters.

        Unlike ``list_for_entity`` this is not anchored to one record — it backs
        the Documents page. Still tenant-scoped by RLS + ``_base``; the service
        gates it on ``documents.view``.
        """
        query = self._base(organization_id)
        if search:
            query = query.where(Attachment.filename.ilike(f"%{search}%"))
        if status:
            query = query.where(Attachment.status == status)
        if entity_type:
            query = query.where(Attachment.entity_type == entity_type)
        query = query.order_by(
            Attachment.created_at.desc(), Attachment.id.desc()
        ).limit(min(limit, MAX_PAGE_SIZE))
        return list((await self.session.execute(query)).unique().scalars().all())

    async def count_by_status(self, organization_id: UUID) -> dict[str, int]:
        """Attachment counts keyed by lifecycle status, for the library stats."""
        query = (
            select(Attachment.status, func.count())
            .where(Attachment.organization_id == organization_id)
            .group_by(Attachment.status)
        )
        rows = (await self.session.execute(query)).all()
        return {row[0]: row[1] for row in rows}

    async def get(self, entity_id: UUID, organization_id: UUID) -> Attachment | None:
        query = self._base(organization_id).where(Attachment.id == entity_id)
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    # ------------------------------------------------- maintenance queries

    async def list_abandoned(
        self, organization_id: UUID, *, before: datetime, limit: int = 500
    ) -> list[Attachment]:
        """Registrations whose upload window has elapsed.

        Oldest first, and batch-limited: a sweep that tried to reap a year's
        backlog in one transaction would hold locks for the duration and lose
        the whole batch to any single failure in it.
        """
        query = (
            select(Attachment)
            .where(Attachment.organization_id == organization_id)
            .where(Attachment.status == "pending_upload")
            .where(Attachment.deleted_at.is_(None))
            .where(Attachment.upload_expires_at.is_not(None))
            .where(Attachment.upload_expires_at < before)
            .order_by(Attachment.upload_expires_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).unique().scalars().all())

    async def list_pending_scan(
        self, organization_id: UUID, *, limit: int = 100
    ) -> list[Attachment]:
        """Uploads awaiting a malware verdict."""
        query = (
            select(Attachment)
            .where(Attachment.organization_id == organization_id)
            .where(Attachment.scan_status == "pending")
            .where(Attachment.deleted_at.is_(None))
            .where(Attachment.storage_key.is_not(None))
            .where(Attachment.size_bytes.is_not(None))
            .order_by(Attachment.created_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).unique().scalars().all())

    async def count_for_entity(
        self, organization_id: UUID, entity_type: str, entity_id: UUID
    ) -> int:
        query = (
            select(func.count())
            .select_from(Attachment)
            .where(Attachment.organization_id == organization_id)
            .where(Attachment.entity_type == entity_type)
            .where(Attachment.entity_id == entity_id)
            .where(Attachment.deleted_at.is_(None))
        )
        return int((await self.session.execute(query)).scalar() or 0)

    async def total_bytes(self, organization_id: UUID) -> int:
        """Sum of stored file sizes for a tenant — the storage-usage meter.

        Sizes that are still NULL (registered but never uploaded) contribute
        nothing, which is correct: an unfinished upload occupies no bucket space.
        """
        query = (
            select(func.coalesce(func.sum(Attachment.size_bytes), 0))
            .where(Attachment.organization_id == organization_id)
            .where(Attachment.deleted_at.is_(None))
        )
        return int((await self.session.execute(query)).scalar() or 0)
