"""User data access."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str, organization_id: UUID) -> User | None:
        """Look up a login candidate within one organization.

        `email` is CITEXT, so comparison is case-insensitive in the database and
        no `lower()` is applied here — doing so would defeat the index.
        """
        query = (
            self._base_query()
            .where(User.email == email)
            .where(User.organization_id == organization_id)
            .options(selectinload(User.organization))
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def lookup_login_identity(self, email: str) -> tuple[UUID, UUID] | None:
        """Resolve `(user_id, organization_id)` for a login attempt.

        Authentication has to find the account before the tenant is known, but
        RLS denies reads without tenant context — a genuine chicken-and-egg.

        This calls a SECURITY DEFINER function that returns *only* the two ids:
        never the password hash, never profile data. The caller then binds the
        tenant context and loads the full row under RLS like any other query.

        A narrow, auditable escape hatch is preferable to exempting `users`
        from row-level security. See migration c3d5e7f9a1b2.
        """
        result = await self.session.execute(
            text(
                "SELECT user_id, organization_id "
                "FROM lookup_login_identity(CAST(:email AS citext))"
            ),
            {"email": email},
        )
        row = result.first()
        return (row[0], row[1]) if row else None

    async def get_with_organization(self, user_id: UUID, organization_id: UUID) -> User | None:
        query = (
            self._base_query()
            .where(User.id == user_id)
            .where(User.organization_id == organization_id)
            .options(selectinload(User.organization))
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def email_exists(self, email: str, organization_id: UUID) -> bool:
        query = (
            select(User.id)
            .where(User.email == email)
            .where(User.organization_id == organization_id)
        )
        result = await self.session.execute(query)
        return result.first() is not None

    # ------------------------------------------------ brute-force state

    async def register_failed_login(
        self, user: User, *, max_attempts: int, lockout_seconds: int
    ) -> None:
        """Increment the failure counter and lock the account at the threshold.

        Per-account, independent of the per-IP limit — a distributed attack on
        one account still trips this.
        """
        user.failed_login_count += 1
        if user.failed_login_count >= max_attempts:
            user.locked_until = datetime.now(UTC) + timedelta(seconds=lockout_seconds)
        await self.session.flush()

    async def register_successful_login(self, user: User) -> None:
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = datetime.now(UTC)
        await self.session.flush()

    @staticmethod
    def is_locked(user: User, *, now: datetime | None = None) -> bool:
        if user.locked_until is None:
            return False
        return user.locked_until > (now or datetime.now(UTC))
