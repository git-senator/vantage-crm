"""API key data access.

Two reads that matter: `lookup_organization`, which resolves the tenant from a
key hash before RLS permits anything (via the SECURITY DEFINER function, exactly
like refresh tokens), and `get_by_hash`, the tenant-scoped fetch that follows.
Everything else is ordinary tenant-scoped access for the management endpoints.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text

from app.models.api_key import ApiKey
from app.repositories.base import BaseRepository

#: last_used_at is only refreshed this often, so a hot key does not incur a
#: write on every single request.
_TOUCH_INTERVAL = timedelta(seconds=60)


class ApiKeyRepository(BaseRepository[ApiKey]):
    model = ApiKey

    async def lookup_organization(self, token_hash: str) -> UUID | None:
        """Resolve a key's tenant before RLS permits any read.

        A machine presents an opaque key with no tenant bound. This calls a
        SECURITY DEFINER function that returns only the organization id; every
        subsequent read happens under RLS. See migration b5e1a3d8c7f2.
        """
        result = await self.session.execute(
            text("SELECT lookup_api_key_organization(:token_hash)"),
            {"token_hash": token_hash},
        )
        return result.scalar_one_or_none()

    async def get_by_hash(self, token_hash: str) -> ApiKey | None:
        """Look up by hash. The raw key is never stored or queried."""
        result = await self.session.execute(
            select(ApiKey).where(ApiKey.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def get(self, key_id: UUID, organization_id: UUID) -> ApiKey | None:
        result = await self.session.execute(
            select(ApiKey)
            .where(ApiKey.id == key_id)
            .where(ApiKey.organization_id == organization_id)
        )
        return result.scalar_one_or_none()

    async def list_for_org(self, organization_id: UUID) -> Sequence[ApiKey]:
        """Every key in the workspace, newest first. Revoked keys are kept so
        their audit trail and last-used record survive."""
        result = await self.session.execute(
            select(ApiKey)
            .where(ApiKey.organization_id == organization_id)
            .order_by(ApiKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def touch_last_used(self, key: ApiKey, *, now: datetime | None = None) -> None:
        """Record use, but at most once per interval — a popular key must not
        take a row-write on every request."""
        moment = now or datetime.now(UTC)
        last = key.last_used_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last is None or (moment - last) >= _TOUCH_INTERVAL:
            key.last_used_at = moment
            await self.session.flush()


__all__ = ["ApiKeyRepository"]
