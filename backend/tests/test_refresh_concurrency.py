"""Refresh concurrency — risk R7.

Strict rotation has a false-positive problem: a client that fires two refreshes
at once has its second request look identical to a replay attack. Detection
fires, the family is revoked, and a legitimate user is logged out for doing
nothing wrong.

Two mechanisms address it, and the tests below pin both:

  * a Redis lock, which serialises a burst (an optimisation), and
  * a grace window, which makes the races that still get through correct.

The critical property is that the grace window must NOT weaken reuse detection
outside the window, and must NOT resurrect an already-revoked family.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.security import hash_refresh_token
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.auth import AuthService, RequestContext
from tests.conftest import VALID_PASSWORD

pytestmark = pytest.mark.integration

CONTEXT = RequestContext(ip_address="203.0.113.10", user_agent="pytest")


class TestGraceWindow:
    async def test_immediate_reuse_is_treated_as_a_race(
        self, db: AsyncSession, settings: Settings, user: User
    ) -> None:
        """The core R7 fix: a spent token re-presented at once must not log out.

        Without the grace window this is indistinguishable from theft and the
        family dies.
        """
        service = AuthService(db, settings)
        first = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        await service.refresh(first.refresh_token, CONTEXT)

        # Same token again, immediately — exactly what a racing client does.
        sibling = await service.refresh(first.refresh_token, CONTEXT)

        assert sibling.refresh_token
        assert sibling.access_token

    async def test_race_stays_in_the_same_family(
        self, db: AsyncSession, settings: Settings, user: User
    ) -> None:
        """A sibling must not start a new lineage, or revocation loses track."""
        service = AuthService(db, settings)
        first = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        await service.refresh(first.refresh_token, CONTEXT)
        await service.refresh(first.refresh_token, CONTEXT)

        families = (
            await db.execute(
                select(RefreshToken.family_id).where(RefreshToken.user_id == user.id)
            )
        ).scalars().all()

        assert len(set(families)) == 1

    async def test_both_siblings_are_usable(
        self, db: AsyncSession, settings: Settings, user: User
    ) -> None:
        """Both racing requests get a working session — neither client breaks."""
        service = AuthService(db, settings)
        first = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)

        a = await service.refresh(first.refresh_token, CONTEXT)
        b = await service.refresh(first.refresh_token, CONTEXT)

        assert await service.refresh(a.refresh_token, CONTEXT)
        assert await service.refresh(b.refresh_token, CONTEXT)

    async def test_expired_grace_still_revokes(
        self, db: AsyncSession, settings: Settings, user: User
    ) -> None:
        """Outside the window, detection is exactly as strict as before.

        This is the test that proves the fix did not quietly disable R7's
        protection in the name of convenience.
        """
        service = AuthService(db, settings)
        first = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        await service.refresh(first.refresh_token, CONTEXT)

        # Backdate the spend to well outside the grace window.
        stored = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(first.refresh_token)
                )
            )
        ).scalar_one()
        stored.used_at = datetime.now(UTC) - timedelta(
            seconds=settings.REFRESH_REUSE_GRACE_SECONDS + 60
        )
        await db.flush()

        with pytest.raises(AuthenticationError):
            await service.refresh(first.refresh_token, CONTEXT)

        await db.rollback()
        rows = (
            await db.execute(
                select(RefreshToken).where(RefreshToken.user_id == user.id)
            )
        ).scalars().all()
        assert all(row.revoked_at is not None for row in rows), (
            "Reuse outside the grace window must still revoke the family."
        )

    async def test_revoked_family_is_never_resurrected(
        self, db: AsyncSession, settings: Settings, user: User
    ) -> None:
        """A replay right after real detection must not slip through the window.

        Without the revoked_at check in `_is_concurrent_refresh`, an attacker
        could keep a compromised family alive by replaying fast enough.
        """
        service = AuthService(db, settings)
        first = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        second = await service.refresh(first.refresh_token, CONTEXT)
        await db.commit()

        # Force a genuine detection by aging the first token out of the window.
        stored = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(first.refresh_token)
                )
            )
        ).scalar_one()
        stored.used_at = datetime.now(UTC) - timedelta(
            seconds=settings.REFRESH_REUSE_GRACE_SECONDS + 60
        )
        await db.commit()

        with pytest.raises(AuthenticationError):
            await service.refresh(first.refresh_token, CONTEXT)
        await db.rollback()
        db.expunge_all()

        # The family is revoked. A fresh, recently-spent sibling must not be
        # accepted just because it falls inside the window.
        with pytest.raises(AuthenticationError):
            await service.refresh(second.refresh_token, CONTEXT)


class TestConcurrentRefresh:
    """Genuinely parallel refreshes, each on its own connection."""

    async def test_ten_parallel_refreshes_do_not_log_the_user_out(
        self, engine, db: AsyncSession, settings: Settings, user: User
    ) -> None:
        """The exact scenario R7 describes.

        Ten requests cross the expiry boundary together. Under strict rotation
        one wins, nine trip detection, the family dies and the user is ejected.
        All ten must now succeed.

        Each task gets its own session, because concurrent work on a single
        AsyncSession is not supported and would test nothing real.
        """
        service = AuthService(db, settings)
        issued = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        await db.commit()

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)

        async def refresh_once() -> bool:
            async with factory() as session:
                try:
                    await AuthService(session, settings).refresh(
                        issued.refresh_token, CONTEXT
                    )
                    await session.commit()
                    return True
                except AuthenticationError:
                    await session.rollback()
                    return False

        results = await asyncio.gather(*(refresh_once() for _ in range(10)))

        assert all(results), (
            f"{results.count(False)}/10 concurrent refreshes were rejected. "
            "A racing client must not be mistaken for a token thief."
        )

    async def test_family_survives_the_burst(
        self, engine, db: AsyncSession, settings: Settings, user: User
    ) -> None:
        """No token in the family may be revoked by a legitimate burst."""
        service = AuthService(db, settings)
        issued = await service.authenticate(user.email, VALID_PASSWORD, CONTEXT)
        await db.commit()

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)

        async def refresh_once() -> None:
            async with factory() as session:
                try:
                    await AuthService(session, settings).refresh(
                        issued.refresh_token, CONTEXT
                    )
                    await session.commit()
                except AuthenticationError:
                    await session.rollback()

        await asyncio.gather(*(refresh_once() for _ in range(8)))

        await db.rollback()
        db.expunge_all()
        revoked = (
            await db.execute(
                select(RefreshToken)
                .where(RefreshToken.user_id == user.id)
                .where(RefreshToken.revoked_at.is_not(None))
            )
        ).scalars().all()

        assert revoked == [], (
            "A concurrent burst revoked tokens. Reuse detection fired on a "
            "legitimate client race."
        )
