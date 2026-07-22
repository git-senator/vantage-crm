"""Attachment data access.

Like notes, an attachment has no scope anchor of its own — visibility follows
the record it hangs off, and the service proves readability of the parent
before listing. Ordering is newest-first within a record.

The two maintenance queries at the bottom are the exception to every other rule
in this file: they run **without tenant scope**, because the background jobs
that call them sweep across all tenants and have no request context to inherit
one from. They are therefore not reachable from any request path, take no
user-supplied predicate, and return only what a job needs to act on. See
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

    async def get(self, entity_id: UUID, organization_id: UUID) -> Attachment | None:
        query = self._base(organization_id).where(Attachment.id == entity_id)
        return (await self.session.execute(query)).unique().scalar_one_or_none()

    # ------------------------------------------------- maintenance queries

    async def list_abandoned(
        self, *, before: datetime, limit: int = 500
    ) -> list[Attachment]:
        """Registrations whose upload window has elapsed, across all tenants.

        Oldest first, and batch-limited: a sweep that tried to reap a year's
        backlog in one transaction would hold locks for the duration and be
        rolled back by any single failure in it.
        """
        query = (
            select(Attachment)
            .where(Attachment.status == "pending_upload")
            .where(Attachment.deleted_at.is_(None))
            .where(Attachment.upload_expires_at.is_not(None))
            .where(Attachment.upload_expires_at < before)
            .order_by(Attachment.upload_expires_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).unique().scalars().all())

    async def list_pending_scan(self, *, limit: int = 100) -> list[Attachment]:
        """Uploads awaiting a malware verdict, across all tenants."""
        query = (
            select(Attachment)
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
