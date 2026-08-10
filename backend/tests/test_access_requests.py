"""Access-request and invitation flow.

Service-level, exercising the whole path a self-service member takes: submit,
approve (which provisions the account and mints the invitation), and accept
(which sets the password and activates). The public API and the review queue are
thin wrappers over `AccessRequestService`; testing the service covers the rules
that matter — single-use tokens, expiry, and the conflict cases — without a
running app.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import verify_password
from app.models.invitation import Invitation
from app.models.user import User
from app.schemas.access import AccessRequestCreate
from app.services.access import AccessRequestService
from app.services.auth import RequestContext
from app.services.rbac import RbacService

pytestmark = pytest.mark.asyncio

CONTEXT = RequestContext(ip_address="203.0.113.7", user_agent="pytest")
NEW_PASSWORD = "a-freshly-chosen-password"


def _form(email: str = "newcomer@example.com") -> AccessRequestCreate:
    return AccessRequestCreate(
        full_name="Nadia Newcomer",
        email=email,
        requested_role="Broker",
        message="Please let me in.",
    )


async def test_submit_records_a_pending_request(db, settings):
    service = AccessRequestService(db, settings)
    request = await service.submit(_form())

    assert request.status == "pending"
    assert request.email == "newcomer@example.com"
    assert request.reviewed_at is None


async def test_approve_provisions_user_role_and_invitation(db, settings, admin):
    _account, auth = admin
    service = AccessRequestService(db, settings)
    request = await service.submit(_form())

    updated, raw_token = await service.approve(request.id, "agent", auth, CONTEXT)

    assert updated.status == "approved"
    assert raw_token  # the one moment the token exists in the clear

    # The account exists but cannot sign in yet.
    user = (
        await db.execute(select(User).where(User.email == "newcomer@example.com"))
    ).scalar_one()
    assert user.status == "invited"

    # It holds the chosen role.
    resolved = await RbacService(db).resolve(
        user.id, user.organization_id, use_cache=False
    )
    assert "agent" in resolved.role_keys

    # An invitation was minted for that user, still open.
    invitation = (
        await db.execute(select(Invitation).where(Invitation.user_id == user.id))
    ).scalar_one()
    assert invitation.accepted_at is None
    assert invitation.role_key == "agent"


async def test_accept_activates_the_account(db, settings, admin):
    _account, auth = admin
    service = AccessRequestService(db, settings)
    request = await service.submit(_form())
    _updated, raw_token = await service.approve(request.id, "agent", auth, CONTEXT)

    user = await service.accept(raw_token, NEW_PASSWORD, CONTEXT)

    assert user.status == "active"
    assert user.password_changed_at is not None
    assert verify_password(NEW_PASSWORD, user.password_hash)


async def test_accept_is_single_use(db, settings, admin):
    _account, auth = admin
    service = AccessRequestService(db, settings)
    request = await service.submit(_form())
    _updated, raw_token = await service.approve(request.id, "agent", auth, CONTEXT)

    await service.accept(raw_token, NEW_PASSWORD, CONTEXT)

    with pytest.raises(NotFoundError):
        await service.accept(raw_token, "another-good-password", CONTEXT)


async def test_expired_invitation_is_refused(db, settings, admin):
    _account, auth = admin
    service = AccessRequestService(db, settings)
    request = await service.submit(_form())
    _updated, raw_token = await service.approve(request.id, "agent", auth, CONTEXT)

    invitation = (
        await db.execute(select(Invitation))
    ).scalar_one()
    invitation.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db.flush()

    with pytest.raises(NotFoundError):
        await service.get_invitation(raw_token)


async def test_unknown_token_is_refused(db, settings):
    service = AccessRequestService(db, settings)
    with pytest.raises(NotFoundError):
        await service.get_invitation("nope-not-a-real-token")


async def test_approving_twice_conflicts(db, settings, admin):
    _account, auth = admin
    service = AccessRequestService(db, settings)
    request = await service.submit(_form())
    await service.approve(request.id, "agent", auth, CONTEXT)

    with pytest.raises(ConflictError):
        await service.approve(request.id, "agent", auth, CONTEXT)


async def test_approving_an_existing_member_conflicts(db, settings, admin):
    account, auth = admin
    service = AccessRequestService(db, settings)
    # A request for an email that already belongs to a member of this org.
    request = await service.submit(_form(email=account.email))

    with pytest.raises(ConflictError):
        await service.approve(request.id, "agent", auth, CONTEXT)


async def test_rejecting_marks_the_request(db, settings, admin):
    _account, auth = admin
    service = AccessRequestService(db, settings)
    request = await service.submit(_form())

    rejected = await service.reject(request.id, auth, CONTEXT)

    assert rejected.status == "rejected"
    assert rejected.reviewed_at is not None


async def test_purge_removes_only_old_decided_requests(db, settings, admin):
    """The queue must shrink, but never lose outstanding work.

    Three rows: a stale rejection, a fresh rejection, and an ancient one nobody
    has looked at. Only the first is the purge's business — the fresh decision
    is still worth showing, and the old pending one is work, not clutter.
    """
    _account, auth = admin
    service = AccessRequestService(db, settings)
    long_ago = datetime.now(UTC) - timedelta(
        days=settings.ACCESS_REQUEST_RETENTION_DAYS + 1
    )

    stale = await service.reject(
        (await service.submit(_form("stale@example.com"))).id, auth, CONTEXT
    )
    stale.reviewed_at = long_ago
    fresh = await service.reject(
        (await service.submit(_form("fresh@example.com"))).id, auth, CONTEXT
    )
    forgotten = await service.submit(_form("forgotten@example.com"))
    forgotten.created_at = long_ago
    await db.flush()

    assert await service.purge_decided() == 1

    surviving = {r.email for r in await service.list_requests()}
    assert surviving == {fresh.email, forgotten.email}


async def test_purge_is_disabled_by_a_zero_window(db, settings, admin):
    _account, auth = admin
    settings.ACCESS_REQUEST_RETENTION_DAYS = 0
    service = AccessRequestService(db, settings)

    decided = await service.reject(
        (await service.submit(_form())).id, auth, CONTEXT
    )
    decided.reviewed_at = datetime.now(UTC) - timedelta(days=3650)
    await db.flush()

    assert await service.purge_decided() == 0
    assert len(await service.list_requests()) == 1
