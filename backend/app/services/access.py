"""Access requests and invitations — provisioning a member from a public form.

The lifecycle this service owns:

    visitor submits  ->  admin approves  ->  user set up (status "invited")
                                          +  single-use invitation link emailed
                                          ->  visitor sets a password
                                          ->  account "active", can sign in

Approval creates the account immediately (in the reviewer's org, with the
chosen role) but withholds a usable password: the person proves control of
their inbox by opening the link and choosing their own password. Nothing ever
travels in the clear.

`access_requests` and `invitations` are tenant-less (see their models). The
submit and accept paths therefore run on an unscoped session; `accept` binds the
invitation's own tenant context before it touches the RLS-protected `users` row.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.security import create_refresh_token, hash_password, hash_refresh_token
from app.db.session import set_tenant_context
from app.models.access_request import AccessRequest
from app.models.invitation import Invitation
from app.models.user import User
from app.schemas.access import AccessRequestCreate
from app.services.audit import AuditService
from app.services.auth import RequestContext
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)


class AccessRequestService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # ------------------------------------------------------------ submit

    async def submit(self, data: AccessRequestCreate) -> AccessRequest:
        """Record a new request. Runs unauthenticated, so it stores and nothing
        more — no account is touched until a member reviews it."""
        request = AccessRequest(
            full_name=data.full_name,
            email=str(data.email),
            requested_role=data.requested_role,
            message=data.message,
        )
        self.session.add(request)
        await self.session.flush()
        logger.info("access_request_submitted", extra={"request_id": str(request.id)})
        return request

    # ------------------------------------------------------------- review

    async def list_requests(self) -> list[AccessRequest]:
        """The review queue: pending first, then most recently created."""
        rows = await self.session.execute(
            select(AccessRequest).order_by(
                # pending (0) ahead of decided (1), newest within each group.
                (AccessRequest.status != "pending"),
                AccessRequest.created_at.desc(),
            )
        )
        return list(rows.scalars().all())

    async def approve(
        self,
        request_id: UUID,
        role_key: str,
        auth: AuthorizationContext,
        context: RequestContext,
    ) -> tuple[AccessRequest, str]:
        """Admit the requester: create their account and mint an invitation.

        Returns the request and the **raw** invitation token — the only moment
        it exists outside the emailed link. The caller sends the email.
        """
        request = await self._get_request(request_id)
        if request.status != "pending":
            raise ConflictError("This request has already been reviewed.")

        organization_id = auth.organization_id

        # An email that already has an account in this org cannot be re-admitted;
        # the org-scoped unique index would reject it anyway, but a clear error
        # beats a database constraint violation.
        existing = await self.session.execute(
            select(User)
            .where(User.organization_id == organization_id)
            .where(User.email == request.email)
            .where(User.deleted_at.is_(None))
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictError("Someone with that email already has an account.")

        # The account exists but cannot sign in yet: a random password nobody
        # holds, and status "invited" until the link is accepted.
        user = User(
            organization_id=organization_id,
            email=request.email,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            full_name=request.full_name,
            status="invited",
        )
        self.session.add(user)
        await self.session.flush()

        await RbacService(self.session).assign_role(
            user_id=user.id,
            role_key=role_key,
            organization_id=organization_id,
            granted_by=auth.user_id,
        )

        token = create_refresh_token()  # 256-bit CSPRNG value + its SHA-256
        invitation = Invitation(
            organization_id=organization_id,
            user_id=user.id,
            email=request.email,
            role_key=role_key,
            token_hash=token.hashed,
            expires_at=datetime.now(UTC)
            + timedelta(hours=self.settings.INVITE_TTL_HOURS),
            created_by_id=auth.user_id,
            access_request_id=request.id,
        )
        self.session.add(invitation)

        request.status = "approved"
        request.reviewed_by_id = auth.user_id
        request.reviewed_at = datetime.now(UTC)
        request.organization_id = organization_id
        await self.session.flush()

        await AuditService(self.session).record(
            action=AuditAction.ACCESS_REQUEST_APPROVED,
            organization_id=organization_id,
            actor_id=auth.user_id,
            entity_type="user",
            entity_id=user.id,
            metadata={"email": request.email, "role": role_key},
            ip_address=context.ip_address,
            user_agent=context.user_agent,
        )
        logger.info(
            "access_request_approved",
            extra={"request_id": str(request.id), "user_id": str(user.id)},
        )
        return request, token.raw

    async def reject(
        self,
        request_id: UUID,
        auth: AuthorizationContext,
        context: RequestContext,
    ) -> AccessRequest:
        request = await self._get_request(request_id)
        if request.status != "pending":
            raise ConflictError("This request has already been reviewed.")

        request.status = "rejected"
        request.reviewed_by_id = auth.user_id
        request.reviewed_at = datetime.now(UTC)
        await self.session.flush()

        await AuditService(self.session).record(
            action=AuditAction.ACCESS_REQUEST_REJECTED,
            organization_id=auth.organization_id,
            actor_id=auth.user_id,
            entity_type="access_request",
            entity_id=request.id,
            metadata={"email": request.email},
            ip_address=context.ip_address,
            user_agent=context.user_agent,
        )
        return request

    # ------------------------------------------------------------- purge

    async def purge_decided(self) -> int:
        """Delete requests decided longer ago than the retention window.

        Two problems, one fix. A queue that only grows stops being a queue: the
        rejected entry from months ago sits under today's work forever, because
        nothing ever removed it. And a rejected applicant is a stranger — we
        hold their name, email and message with no relationship that justifies
        keeping them, which is exactly what data-protection law asks us not to
        do.

        Only *decided* requests go. A pending one, however old, is outstanding
        work and deleting it would hide the work rather than tidy it. The
        decision itself is not lost either: approve and reject each write an
        audit entry, which lives under the tenant's own retention policy.
        """
        days = self.settings.ACCESS_REQUEST_RETENTION_DAYS
        if days <= 0:
            return 0

        cutoff = datetime.now(UTC) - timedelta(days=days)
        result = await self.session.execute(
            delete(AccessRequest).where(
                AccessRequest.status != "pending",
                # reviewed_at is set on every decision; created_at is the
                # fallback for any row predating that column being populated.
                func.coalesce(AccessRequest.reviewed_at, AccessRequest.created_at)
                < cutoff,
            )
        )
        deleted = result.rowcount or 0
        if deleted:
            logger.info("access_requests_purged", extra={"deleted": deleted})
        return deleted

    # ------------------------------------------------------------- accept

    async def get_invitation(self, raw_token: str) -> Invitation:
        """Resolve a live invitation from its raw token, or refuse.

        A used, expired, or unknown token all raise the same NotFound — the
        accept page must not distinguish "never existed" from "already spent".
        """
        invitation = await self._lookup(raw_token)
        if (
            invitation is None
            or not invitation.is_usable
            or invitation.expires_at <= datetime.now(UTC)
        ):
            raise NotFoundError("This invitation is invalid or has expired.")
        return invitation

    async def accept(
        self, raw_token: str, password: str, context: RequestContext
    ) -> User:
        """Set the password and activate the account.

        Runs on an unscoped session: the invitation is read tenant-lessly, then
        its own org is bound before the `users` write so RLS is satisfied.
        """
        invitation = await self.get_invitation(raw_token)

        await set_tenant_context(
            self.session, invitation.organization_id, invitation.user_id
        )
        user = (
            await self.session.execute(
                select(User)
                .where(User.id == invitation.user_id)
                .where(User.organization_id == invitation.organization_id)
            )
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError("This invitation is invalid or has expired.")

        now = datetime.now(UTC)
        user.password_hash = hash_password(password)
        user.status = "active"
        user.password_changed_at = now
        invitation.accepted_at = now
        await self.session.flush()

        await AuditService(self.session).record(
            action=AuditAction.INVITATION_ACCEPTED,
            organization_id=invitation.organization_id,
            actor_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
        )
        logger.info("invitation_accepted", extra={"user_id": str(user.id)})
        return user

    # --------------------------------------------------------------- util

    async def _get_request(self, request_id: UUID) -> AccessRequest:
        request = await self.session.get(AccessRequest, request_id)
        if request is None:
            raise NotFoundError("Access request not found.")
        return request

    async def _lookup(self, raw_token: str) -> Invitation | None:
        return (
            await self.session.execute(
                select(Invitation).where(
                    Invitation.token_hash == hash_refresh_token(raw_token)
                )
            )
        ).scalar_one_or_none()
