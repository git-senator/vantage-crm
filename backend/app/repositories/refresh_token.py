"""Refresh token data access, including family revocation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, select, update

from app.models.refresh_token import RefreshToken
from app.repositories.base import BaseRepository


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Look up by hash. The raw token is never stored or queried."""
        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def revoke_family(self, family_id: UUID, reason: str) -> int:
        """Revoke every live token in a lineage. Returns the number affected.

        Called on logout (ends one session) and on reuse detection (kills a
        possibly-compromised lineage). Already-revoked rows are left untouched
        so the original reason survives for forensics.
        """
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == family_id)
                .where(RefreshToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
            ),
        )
        return result.rowcount or 0

    async def revoke_all_for_user(self, user_id: UUID, reason: str) -> int:
        """Log a user out everywhere. Used on password change and deactivation."""
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == user_id)
                .where(RefreshToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
            ),
        )
        return result.rowcount or 0

    async def mark_used(self, token: RefreshToken, replaced_by: RefreshToken) -> None:
        token.used_at = datetime.now(UTC)
        token.replaced_by_id = replaced_by.id
        await self.session.flush()

    async def delete_expired(self, *, before: datetime | None = None) -> int:
        """Housekeeping for the scheduled sweep in Phase 3."""
        cutoff = before or datetime.now(UTC)
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(RefreshToken)
                .where(RefreshToken.expires_at < cutoff)
                .where(RefreshToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC), revoked_reason="expired")
            ),
        )
        return result.rowcount or 0
