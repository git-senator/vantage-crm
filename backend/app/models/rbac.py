"""Roles, permissions and their assignments.

Roles are rows, not code. That is what makes custom roles a UI feature later
rather than a deployment: an admin composes a role from existing permissions
and nothing needs to ship.

`permissions` is deliberately global rather than per-tenant — the vocabulary is
defined by the application, not by a customer. Only `roles` carries an
organization_id, and it is NULL for the built-in system roles shared by every
workspace.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Permission(Base, UUIDPrimaryKeyMixin):
    """A single capability, e.g. `leads.manage`.

    Global, not tenant-scoped: the permission vocabulary belongs to the
    application. Seeded from app.core.permissions.
    """

    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    resource: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)

    __table_args__ = (
        Index("ix_permissions_resource", "resource"),
        CheckConstraint("key = resource || '.' || action", name="ck_permissions_key"),
    )

    def __repr__(self) -> str:
        return f"<Permission {self.key}>"


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A named bundle of permissions.

    `organization_id IS NULL` marks a built-in system role shared by every
    workspace. Custom roles carry their tenant.
    """

    __tablename__ = "roles"

    organization_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    key: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="")

    # System roles cannot be edited or deleted through the API.
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Owner additionally cannot be removed from its last holder.
    is_protected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        # NULLS NOT DISTINCT: without it, two system roles could share a key
        # because NULL != NULL in a unique index.
        UniqueConstraint(
            "organization_id",
            "key",
            name="uq_roles_org_key",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name="ck_roles_key_format"),
    )

    def __repr__(self) -> str:
        return f"<Role {self.key}>"


class RolePermission(Base):
    """A grant: role -> permission, at a scope.

    Scope lives on the grant rather than in the permission key. `leads.view`
    at OWN and `leads.view` at TEAM are the same capability with different
    reach, and encoding reach in the key would double the permission table.
    """

    __tablename__ = "role_permissions"

    role_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="own")

    role: Mapped[Role] = relationship(back_populates="permissions")
    permission: Mapped[Permission] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("scope IN ('own', 'team', 'all')", name="ck_role_permissions_scope"),
    )


class UserRole(Base):
    """Assignment of a role to a user.

    Carries `organization_id` so the row is covered by the same RLS policy as
    everything else — without it, a role assignment would be the one table in
    the system outside the tenant boundary.
    """

    __tablename__ = "user_roles"

    user_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    granted_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    role: Mapped[Role] = relationship(lazy="selectin")

    __table_args__ = (Index("ix_user_roles_user", "user_id"),)


class Team(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A group of users, used to resolve `Scope.TEAM`.

    Without teams, TEAM scope has no meaning; a manager would fall back to
    seeing only their own records.
    """

    __tablename__ = "teams"

    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    lead_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_teams_org_name"),
    )


class TeamMember(Base):
    __tablename__ = "team_members"

    team_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    organization_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    __table_args__ = (Index("ix_team_members_user", "user_id"),)
