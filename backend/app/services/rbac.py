"""Authorization: resolving what a user may do, and to which records.

Two independent questions (docs/SECURITY.md §1):

    can(permission)        -> is the action allowed at all?
    scope_for(permission)  -> which rows?

The second must become a SQL predicate, never a post-fetch filter. Filtering
after the query returns wrong pagination totals, leaks the existence of records
through counts, and degrades linearly with table size.

Resolved permissions are cached in Redis because they are read on every
authorized request. The cache is invalidated on any role or assignment change,
and carries a short TTL so a missed invalidation self-heals rather than
stranding a user with stale access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import PermissionDeniedError
from app.core.logging import get_logger
from app.core.permissions import Scope
from app.core.redis import get_redis
from app.models.rbac import Permission, Role, RolePermission, TeamMember, UserRole

logger = get_logger(__name__)

# Short enough that a missed invalidation corrects itself quickly; long enough
# to absorb the request burst of a page load.
PERMISSION_CACHE_TTL_SECONDS = 300


def _cache_key(user_id: UUID) -> str:
    return f"rbac:permissions:{user_id}"


@dataclass(frozen=True, slots=True)
class AuthorizationContext:
    """Everything needed to authorize a request, resolved once per request."""

    user_id: UUID
    organization_id: UUID
    role_keys: tuple[str, ...]
    #: permission key -> widest granted scope
    grants: dict[str, Scope]

    def can(self, permission: str) -> bool:
        return permission in self.grants

    def scope_for(self, permission: str) -> Scope | None:
        return self.grants.get(permission)

    def require(self, permission: str) -> Scope:
        """Assert a permission and return its scope.

        Raising here rather than returning a bool means a caller cannot forget
        to check the result — a silent `if can(...)` that nobody reads is the
        classic broken-access-control bug.
        """
        scope = self.grants.get(permission)
        if scope is None:
            logger.warning(
                "permission_denied",
                extra={
                    "user_id": str(self.user_id),
                    "permission": permission,
                    "roles": list(self.role_keys),
                },
            )
            raise PermissionDeniedError(
                f"This action requires the '{permission}' permission."
            )
        return scope

    @property
    def permission_keys(self) -> list[str]:
        return sorted(self.grants)


class RbacService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------- resolution

    async def resolve(
        self, user_id: UUID, organization_id: UUID, *, use_cache: bool = True
    ) -> AuthorizationContext:
        if use_cache:
            cached = await self._read_cache(user_id, organization_id)
            if cached is not None:
                return cached

        context = await self._resolve_from_database(user_id, organization_id)

        if use_cache:
            await self._write_cache(context)
        return context

    async def _resolve_from_database(
        self, user_id: UUID, organization_id: UUID
    ) -> AuthorizationContext:
        rows = (
            await self.session.execute(
                select(Role.key, Permission.key, RolePermission.scope)
                .select_from(UserRole)
                .join(Role, Role.id == UserRole.role_id)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(UserRole.user_id == user_id)
                .where(UserRole.organization_id == organization_id)
            )
        ).all()

        role_keys: set[str] = set()
        grants: dict[str, Scope] = {}

        for role_key, permission_key, scope_value in rows:
            role_keys.add(role_key)
            scope = Scope(scope_value)
            existing = grants.get(permission_key)
            # Union, not intersection: holding two roles must never reduce
            # access below what either grants alone.
            grants[permission_key] = (
                Scope.widest([existing, scope]) if existing else scope
            )

        return AuthorizationContext(
            user_id=user_id,
            organization_id=organization_id,
            role_keys=tuple(sorted(role_keys)),
            grants=grants,
        )

    # ------------------------------------------------------------ scope

    async def team_member_ids(self, user_id: UUID, organization_id: UUID) -> list[UUID]:
        """Every user sharing a team with this user, including themselves.

        Used to turn `Scope.TEAM` into a WHERE clause. Always contains the user
        so a manager on no team still sees their own records rather than
        nothing.
        """
        team_ids = (
            await self.session.execute(
                select(TeamMember.team_id)
                .where(TeamMember.user_id == user_id)
                .where(TeamMember.organization_id == organization_id)
            )
        ).scalars().all()

        if not team_ids:
            return [user_id]

        member_ids = (
            await self.session.execute(
                select(TeamMember.user_id)
                .where(TeamMember.team_id.in_(team_ids))
                .where(TeamMember.organization_id == organization_id)
            )
        ).scalars().all()

        return list({*member_ids, user_id})

    async def owner_ids_for_scope(
        self, context: AuthorizationContext, scope: Scope
    ) -> list[UUID] | None:
        """Owner ids a scope permits, or None for "no restriction".

        Repositories turn this into `WHERE owner_id IN (...)`. Returning None
        for ALL avoids generating a pointless IN clause over every user.
        """
        match scope:
            case Scope.OWN:
                return [context.user_id]
            case Scope.TEAM:
                return await self.team_member_ids(
                    context.user_id, context.organization_id
                )
            case Scope.ALL:
                return None

    # ------------------------------------------------------- assignment

    async def assign_role(
        self,
        *,
        user_id: UUID,
        role_key: str,
        organization_id: UUID,
        granted_by: UUID | None = None,
    ) -> None:
        role = await self._find_role(role_key, organization_id)
        if role is None:
            raise PermissionDeniedError(f"Unknown role '{role_key}'.")

        exists = (
            await self.session.execute(
                select(UserRole)
                .where(UserRole.user_id == user_id)
                .where(UserRole.role_id == role.id)
            )
        ).scalar_one_or_none()

        if exists is None:
            self.session.add(
                UserRole(
                    user_id=user_id,
                    role_id=role.id,
                    organization_id=organization_id,
                    granted_by=granted_by,
                )
            )
            await self.session.flush()

        await self.invalidate(user_id)
        logger.info(
            "role_assigned",
            extra={"user_id": str(user_id), "role": role_key, "granted_by": str(granted_by)},
        )

    async def revoke_role(
        self, *, user_id: UUID, role_key: str, organization_id: UUID
    ) -> None:
        role = await self._find_role(role_key, organization_id)
        if role is None:
            return

        assignment = (
            await self.session.execute(
                select(UserRole)
                .where(UserRole.user_id == user_id)
                .where(UserRole.role_id == role.id)
            )
        ).scalar_one_or_none()

        if assignment is not None:
            await self.session.delete(assignment)
            await self.session.flush()

        await self.invalidate(user_id)
        logger.info("role_revoked", extra={"user_id": str(user_id), "role": role_key})

    async def _find_role(self, role_key: str, organization_id: UUID) -> Role | None:
        """Prefer a workspace's custom role over the system role of the same key."""
        result = await self.session.execute(
            select(Role)
            .where(Role.key == role_key)
            .where(
                (Role.organization_id == organization_id)
                | (Role.organization_id.is_(None))
            )
            .order_by(Role.organization_id.is_(None))
            .options(selectinload(Role.permissions))
        )
        return result.scalars().first()

    async def list_roles(self, organization_id: UUID) -> list[Role]:
        result = await self.session.execute(
            select(Role)
            .where(
                (Role.organization_id == organization_id)
                | (Role.organization_id.is_(None))
            )
            .order_by(Role.name)
            .options(selectinload(Role.permissions))
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------ cache

    async def _read_cache(
        self, user_id: UUID, organization_id: UUID
    ) -> AuthorizationContext | None:
        try:
            raw = await get_redis().get(_cache_key(user_id))
        except Exception:
            # Cache unavailability must never deny access — fall through to
            # the database rather than failing the request.
            logger.warning("rbac_cache_unavailable", exc_info=True)
            return None

        if not raw:
            return None

        try:
            payload = json.loads(raw)
            if payload.get("org") != str(organization_id):
                return None
            return AuthorizationContext(
                user_id=user_id,
                organization_id=organization_id,
                role_keys=tuple(payload["roles"]),
                grants={k: Scope(v) for k, v in payload["grants"].items()},
            )
        except (ValueError, KeyError):
            return None

    async def _write_cache(self, context: AuthorizationContext) -> None:
        payload = {
            "org": str(context.organization_id),
            "roles": list(context.role_keys),
            "grants": {k: v.value for k, v in context.grants.items()},
        }
        try:
            await get_redis().setex(
                _cache_key(context.user_id),
                PERMISSION_CACHE_TTL_SECONDS,
                json.dumps(payload),
            )
        except Exception:
            logger.warning("rbac_cache_write_failed", exc_info=True)

    @staticmethod
    async def invalidate(user_id: UUID) -> None:
        """Drop a user's cached permissions after any change to their access."""
        try:
            await get_redis().delete(_cache_key(user_id))
        except Exception:
            # The TTL bounds the damage: stale access expires within 5 minutes.
            logger.warning("rbac_cache_invalidate_failed", exc_info=True)
