"""ORM models.

Every model must be imported here. Alembic autogenerate compares
`Base.metadata` against the live schema, and a model that is never imported is
absent from that metadata — autogenerate would then emit a migration that drops
its table.
"""

from app.models.audit import AuditLog
from app.models.client import Client
from app.models.lead import Lead
from app.models.organization import Organization
from app.models.property import Property
from app.models.rbac import (
    Permission,
    Role,
    RolePermission,
    Team,
    TeamMember,
    UserRole,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__ = [
    "AuditLog",
    "Client",
    "Lead",
    "Organization",
    "Permission",
    "Property",
    "RefreshToken",
    "Role",
    "RolePermission",
    "Team",
    "TeamMember",
    "User",
    "UserRole",
]
