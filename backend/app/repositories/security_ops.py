"""Security operations data access. Tenant-scoped reads on top of RLS."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from app.models.security_ops import SecurityAlert, SecurityEvent, TrustedDevice
from app.repositories.base import BaseRepository
from app.schemas.common import MAX_PAGE_SIZE, Cursor


class SecurityEventRepository(BaseRepository[SecurityEvent]):
    model = SecurityEvent

    async def list_for_org(
        self,
        organization_id: UUID,
        *,
        limit: int,
        cursor: Cursor | None = None,
        event_type: str | None = None,
        severity: str | None = None,
        user_id: UUID | None = None,
    ) -> tuple[list[SecurityEvent], bool]:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = select(SecurityEvent).where(
            SecurityEvent.organization_id == organization_id
        )
        if event_type:
            query = query.where(SecurityEvent.event_type == event_type)
        if severity:
            query = query.where(SecurityEvent.severity == severity)
        if user_id:
            query = query.where(SecurityEvent.user_id == user_id)
        if cursor is not None:
            query = query.where(
                func.row(SecurityEvent.created_at, SecurityEvent.id)
                < func.row(cursor.created_at, cursor.id)
            )
        query = query.order_by(
            SecurityEvent.created_at.desc(), SecurityEvent.id.desc()
        ).limit(limit + 1)
        rows = list((await self.session.execute(query)).scalars().all())
        return rows[:limit], len(rows) > limit

    async def count_recent_failed_logins(
        self, organization_id: UUID, user_id: UUID | None, *, since: datetime
    ) -> int:
        query = (
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.organization_id == organization_id)
            .where(SecurityEvent.event_type == "login.failed")
            .where(SecurityEvent.created_at >= since)
        )
        if user_id is not None:
            query = query.where(SecurityEvent.user_id == user_id)
        return int((await self.session.execute(query)).scalar() or 0)

    async def count_by_severity(
        self, organization_id: UUID, *, since: datetime
    ) -> dict[str, int]:
        query = (
            select(SecurityEvent.severity, func.count())
            .where(SecurityEvent.organization_id == organization_id)
            .where(SecurityEvent.created_at >= since)
            .group_by(SecurityEvent.severity)
        )
        rows = (await self.session.execute(query)).all()
        return {row[0]: row[1] for row in rows}


class SecurityAlertRepository(BaseRepository[SecurityAlert]):
    model = SecurityAlert

    async def find_open_by_dedup(
        self, organization_id: UUID, dedup_key: str
    ) -> SecurityAlert | None:
        query = (
            select(SecurityAlert)
            .where(SecurityAlert.organization_id == organization_id)
            .where(SecurityAlert.dedup_key == dedup_key)
            .where(SecurityAlert.status.in_(("open", "acknowledged")))
            .order_by(SecurityAlert.last_seen_at.desc())
        )
        return (await self.session.execute(query)).scalars().first()

    async def list_for_org(
        self,
        organization_id: UUID,
        *,
        limit: int,
        cursor: Cursor | None = None,
        status: str | None = None,
        severity: str | None = None,
    ) -> tuple[list[SecurityAlert], bool]:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = select(SecurityAlert).where(
            SecurityAlert.organization_id == organization_id
        )
        if status:
            query = query.where(SecurityAlert.status == status)
        if severity:
            query = query.where(SecurityAlert.severity == severity)
        if cursor is not None:
            query = query.where(
                func.row(SecurityAlert.created_at, SecurityAlert.id)
                < func.row(cursor.created_at, cursor.id)
            )
        query = query.order_by(
            SecurityAlert.created_at.desc(), SecurityAlert.id.desc()
        ).limit(limit + 1)
        rows = list((await self.session.execute(query)).scalars().all())
        return rows[:limit], len(rows) > limit

    async def count_by_status(self, organization_id: UUID) -> dict[str, int]:
        query = (
            select(SecurityAlert.status, func.count())
            .where(SecurityAlert.organization_id == organization_id)
            .group_by(SecurityAlert.status)
        )
        rows = (await self.session.execute(query)).all()
        return {row[0]: row[1] for row in rows}


class TrustedDeviceRepository(BaseRepository[TrustedDevice]):
    model = TrustedDevice

    async def get_for_fingerprint(
        self, organization_id: UUID, user_id: UUID, fingerprint: str
    ) -> TrustedDevice | None:
        query = (
            select(TrustedDevice)
            .where(TrustedDevice.organization_id == organization_id)
            .where(TrustedDevice.user_id == user_id)
            .where(TrustedDevice.device_fingerprint == fingerprint)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list_for_org(
        self, organization_id: UUID, *, user_id: UUID | None = None
    ) -> Sequence[TrustedDevice]:
        query = select(TrustedDevice).where(
            TrustedDevice.organization_id == organization_id
        )
        if user_id is not None:
            query = query.where(TrustedDevice.user_id == user_id)
        query = query.order_by(TrustedDevice.last_seen_at.desc())
        return list((await self.session.execute(query)).scalars().all())


__all__ = [
    "SecurityAlertRepository",
    "SecurityEventRepository",
    "TrustedDeviceRepository",
]
