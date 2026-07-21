"""Authorization: permissions, scopes, and role resolution.

The property under test throughout is that permission and scope are separate
dimensions. Collapsing them is the usual RBAC mistake and produces a role
explosion; these tests pin the separation.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedError
from app.core.permissions import (
    ALL_PERMISSION_KEYS,
    PERMISSIONS,
    SYSTEM_ROLES,
    SYSTEM_ROLES_BY_KEY,
    Scope,
    validate_registry,
)
from app.core.security import hash_password
from app.models.organization import Organization
from app.models.rbac import Permission, Role, RolePermission, Team, TeamMember, UserRole
from app.models.user import User
from app.services.rbac import AuthorizationContext, RbacService

# ------------------------------------------------------------- unit tests


class TestScope:
    def test_ordering_is_narrowest_to_widest(self) -> None:
        assert Scope.OWN.rank < Scope.TEAM.rank < Scope.ALL.rank

    def test_widest_wins(self) -> None:
        assert Scope.widest([Scope.OWN, Scope.ALL]) == Scope.ALL
        assert Scope.widest([Scope.OWN, Scope.TEAM]) == Scope.TEAM

    def test_widest_of_empty_defaults_to_own(self) -> None:
        assert Scope.widest([]) == Scope.OWN


class TestRegistry:
    def test_registry_is_self_consistent(self) -> None:
        """Every role grant must name a permission that exists."""
        validate_registry()

    def test_permission_keys_are_unique(self) -> None:
        keys = [p.key for p in PERMISSIONS]
        assert len(keys) == len(set(keys))

    def test_key_matches_resource_and_action(self) -> None:
        """The database enforces this too, via a CHECK constraint."""
        for permission in PERMISSIONS:
            assert permission.key == f"{permission.resource}.{permission.action}"

    def test_required_roles_exist(self) -> None:
        assert set(SYSTEM_ROLES_BY_KEY) == {"owner", "admin", "manager", "agent"}

    def test_owner_has_every_permission(self) -> None:
        assert set(SYSTEM_ROLES_BY_KEY["owner"].grants) == set(ALL_PERMISSION_KEYS)

    def test_admin_cannot_manage_billing(self) -> None:
        """The one thing that separates admin from owner."""
        assert "billing.manage" not in SYSTEM_ROLES_BY_KEY["admin"].grants

    def test_agent_scope_is_asymmetric(self) -> None:
        """The clearest demonstration that scope is not part of the permission.

        Every agent can see all listings — shared inventory — but may only
        edit their own. Same resource, two different scopes.
        """
        agent = SYSTEM_ROLES_BY_KEY["agent"].grants
        assert agent["properties.view"] == Scope.ALL
        assert agent["properties.manage"] == Scope.OWN

    def test_manager_widens_agent_rather_than_replacing_it(self) -> None:
        agent = SYSTEM_ROLES_BY_KEY["agent"].grants
        manager = SYSTEM_ROLES_BY_KEY["manager"].grants

        assert set(agent).issubset(set(manager))
        assert manager["leads.view"] == Scope.TEAM
        assert agent["leads.view"] == Scope.OWN

    def test_only_owner_is_protected(self) -> None:
        protected = {r.key for r in SYSTEM_ROLES if r.is_protected}
        assert protected == {"owner"}


class TestAuthorizationContext:
    def _context(self, **grants: Scope) -> AuthorizationContext:
        import uuid

        return AuthorizationContext(
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            role_keys=("agent",),
            grants=dict(grants),
        )

    def test_can_reports_held_permissions(self) -> None:
        context = self._context(**{"leads.view": Scope.OWN})
        assert context.can("leads.view")
        assert not context.can("leads.manage")

    def test_require_returns_the_scope(self) -> None:
        context = self._context(**{"leads.view": Scope.TEAM})
        assert context.require("leads.view") == Scope.TEAM

    def test_require_raises_when_missing(self) -> None:
        """Raising, not returning False — a caller cannot forget to check."""
        with pytest.raises(PermissionDeniedError, match=re.escape("leads.manage")):
            self._context().require("leads.manage")

    def test_denial_message_does_not_leak_other_permissions(self) -> None:
        context = self._context(**{"billing.manage": Scope.ALL})
        with pytest.raises(PermissionDeniedError) as exc:
            context.require("leads.view")
        assert "billing" not in str(exc.value)


# ------------------------------------------------------ integration tests

pytest_integration = pytest.mark.integration


async def _seed_rbac(db: AsyncSession) -> None:
    """Seed permissions and system roles, mirroring migration d7305fe801ac."""
    for definition in PERMISSIONS:
        db.add(
            Permission(
                key=definition.key,
                resource=definition.resource,
                action=definition.action,
                description=definition.description,
            )
        )
    await db.flush()

    permissions = {
        p.key: p for p in (await db.execute(select(Permission))).scalars().all()
    }

    for role_definition in SYSTEM_ROLES:
        role = Role(
            organization_id=None,
            key=role_definition.key,
            name=role_definition.name,
            description=role_definition.description,
            is_system=True,
            is_protected=role_definition.is_protected,
        )
        db.add(role)
        await db.flush()
        for permission_key, scope in role_definition.grants.items():
            db.add(
                RolePermission(
                    role_id=role.id,
                    permission_id=permissions[permission_key].id,
                    scope=scope.value,
                )
            )
    await db.flush()


@pytest.fixture
async def rbac(db: AsyncSession) -> RbacService:
    await _seed_rbac(db)
    return RbacService(db)


@pytest_integration
class TestRoleResolution:
    async def test_user_without_roles_has_no_permissions(
        self, rbac: RbacService, user: User
    ) -> None:
        """Deny by default."""
        context = await rbac.resolve(user.id, user.organization_id, use_cache=False)
        assert context.grants == {}
        assert not context.can("leads.view")

    async def test_agent_role_grants_own_scope(
        self, rbac: RbacService, user: User
    ) -> None:
        await rbac.assign_role(
            user_id=user.id, role_key="agent", organization_id=user.organization_id
        )
        context = await rbac.resolve(user.id, user.organization_id, use_cache=False)

        assert "agent" in context.role_keys
        assert context.scope_for("leads.view") == Scope.OWN
        assert context.scope_for("properties.view") == Scope.ALL

    async def test_owner_role_grants_everything(
        self, rbac: RbacService, user: User
    ) -> None:
        await rbac.assign_role(
            user_id=user.id, role_key="owner", organization_id=user.organization_id
        )
        context = await rbac.resolve(user.id, user.organization_id, use_cache=False)

        assert set(context.grants) == set(ALL_PERMISSION_KEYS)
        assert all(scope == Scope.ALL for scope in context.grants.values())

    async def test_multiple_roles_take_the_widest_scope(
        self, rbac: RbacService, user: User
    ) -> None:
        """Union, not intersection — adding a role must never remove access."""
        for role_key in ("agent", "manager"):
            await rbac.assign_role(
                user_id=user.id,
                role_key=role_key,
                organization_id=user.organization_id,
            )
        context = await rbac.resolve(user.id, user.organization_id, use_cache=False)

        # agent grants OWN, manager grants TEAM; TEAM must win.
        assert context.scope_for("leads.view") == Scope.TEAM

    async def test_revoking_a_role_removes_its_permissions(
        self, rbac: RbacService, user: User
    ) -> None:
        await rbac.assign_role(
            user_id=user.id, role_key="admin", organization_id=user.organization_id
        )
        assert (
            await rbac.resolve(user.id, user.organization_id, use_cache=False)
        ).can("users.manage")

        await rbac.revoke_role(
            user_id=user.id, role_key="admin", organization_id=user.organization_id
        )
        assert not (
            await rbac.resolve(user.id, user.organization_id, use_cache=False)
        ).can("users.manage")

    async def test_assignment_is_idempotent(
        self, rbac: RbacService, db: AsyncSession, user: User
    ) -> None:
        for _ in range(3):
            await rbac.assign_role(
                user_id=user.id,
                role_key="agent",
                organization_id=user.organization_id,
            )

        rows = (
            await db.execute(select(UserRole).where(UserRole.user_id == user.id))
        ).scalars().all()
        assert len(rows) == 1

    async def test_unknown_role_is_rejected(
        self, rbac: RbacService, user: User
    ) -> None:
        with pytest.raises(PermissionDeniedError):
            await rbac.assign_role(
                user_id=user.id,
                role_key="superuser",
                organization_id=user.organization_id,
            )


@pytest_integration
class TestScopeResolution:
    """Scope must become a set of owner ids that a repository turns into SQL."""

    async def test_own_scope_is_just_the_user(
        self, rbac: RbacService, user: User
    ) -> None:
        context = await rbac.resolve(user.id, user.organization_id, use_cache=False)
        owners = await rbac.owner_ids_for_scope(context, Scope.OWN)
        assert owners == [user.id]

    async def test_all_scope_returns_none(
        self, rbac: RbacService, user: User
    ) -> None:
        """None means "no predicate" — avoids an IN clause over every user."""
        context = await rbac.resolve(user.id, user.organization_id, use_cache=False)
        assert await rbac.owner_ids_for_scope(context, Scope.ALL) is None

    async def test_team_scope_includes_teammates(
        self, rbac: RbacService, db: AsyncSession, user: User
    ) -> None:
        teammate = User(
            organization_id=user.organization_id,
            email="teammate@vantage.example",
            password_hash=hash_password("correct-horse-battery-staple"),
            full_name="Team Mate",
        )
        db.add(teammate)
        await db.flush()

        team = Team(organization_id=user.organization_id, name="Westside")
        db.add(team)
        await db.flush()
        for member in (user, teammate):
            db.add(
                TeamMember(
                    team_id=team.id,
                    user_id=member.id,
                    organization_id=user.organization_id,
                )
            )
        await db.flush()

        context = await rbac.resolve(user.id, user.organization_id, use_cache=False)
        owners = await rbac.owner_ids_for_scope(context, Scope.TEAM)

        assert owners is not None
        assert set(owners) == {user.id, teammate.id}

    async def test_team_scope_without_a_team_falls_back_to_self(
        self, rbac: RbacService, user: User
    ) -> None:
        """A manager on no team must see their own records, not nothing."""
        context = await rbac.resolve(user.id, user.organization_id, use_cache=False)
        owners = await rbac.owner_ids_for_scope(context, Scope.TEAM)
        assert owners == [user.id]

    async def test_team_scope_does_not_cross_tenants(
        self,
        rbac: RbacService,
        db: AsyncSession,
        user: User,
        other_organization: Organization,
    ) -> None:
        """A team in another workspace must never widen this user's scope."""
        outsider = User(
            organization_id=other_organization.id,
            email="outsider@meridian.example",
            password_hash=hash_password("correct-horse-battery-staple"),
            full_name="Out Sider",
        )
        db.add(outsider)
        await db.flush()

        foreign_team = Team(organization_id=other_organization.id, name="Foreign")
        db.add(foreign_team)
        await db.flush()
        db.add(
            TeamMember(
                team_id=foreign_team.id,
                user_id=outsider.id,
                organization_id=other_organization.id,
            )
        )
        await db.flush()

        context = await rbac.resolve(user.id, user.organization_id, use_cache=False)
        owners = await rbac.owner_ids_for_scope(context, Scope.TEAM)

        assert owners is not None
        assert outsider.id not in owners
