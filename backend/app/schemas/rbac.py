"""RBAC contracts."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PermissionGrant(BaseModel):
    key: str
    scope: str


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    name: str
    description: str
    is_system: bool
    is_protected: bool
    permissions: list[PermissionGrant]


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    resource: str
    action: str
    description: str


class RoleAssignmentRequest(BaseModel):
    role_key: str
