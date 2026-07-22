"""Permission registry and system role definitions.

Two dimensions, evaluated independently (docs/SECURITY.md §1):

    CAN this actor perform this action?   -> permission check
    WHICH records may they touch?         -> scope (own | team | all)

Collapsing them into one check is the usual mistake. It forces a role
explosion — `agent_own_leads`, `manager_team_leads`, `admin_all_leads` — where
a 2xN matrix would do, and every new resource multiplies the role count.

This module is the source of truth. The database is seeded from it, so adding a
permission means editing one list here, not writing SQL. Roles themselves are
rows rather than code, which is what makes custom roles a UI feature later
rather than a deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Scope(StrEnum):
    """Which records a grant reaches.

    Ordered narrowest to widest; `Scope.widest` relies on that ordering.
    """

    OWN = "own"
    TEAM = "team"
    ALL = "all"

    @property
    def rank(self) -> int:
        return {Scope.OWN: 0, Scope.TEAM: 1, Scope.ALL: 2}[self]

    @classmethod
    def widest(cls, scopes: list[Scope]) -> Scope:
        """The most permissive of several grants.

        A user with two roles gets the union of their access, not the
        intersection — otherwise adding a role could remove access, which
        nobody expects.
        """
        return max(scopes, key=lambda scope: scope.rank) if scopes else cls.OWN


@dataclass(frozen=True, slots=True)
class Permission:
    key: str
    resource: str
    action: str
    description: str


def _permission(resource: str, action: str, description: str) -> Permission:
    return Permission(
        key=f"{resource}.{action}",
        resource=resource,
        action=action,
        description=description,
    )


# ---------------------------------------------------------------- registry
#
# `view` is read. `manage` is create/update/delete. Splitting every verb
# (create/update/delete separately) produces a permission matrix nobody can
# reason about in a UI; `view`/`manage` is the granularity real CRM admins
# actually configure. Genuinely distinct high-risk actions get their own key
# (`deals.approve`, `documents.sign`).

PERMISSIONS: tuple[Permission, ...] = (
    # --- CRM entities ---
    _permission("leads", "view", "View leads"),
    _permission("leads", "manage", "Create, update and delete leads"),
    _permission("leads", "assign", "Reassign a lead to another user"),
    _permission("contacts", "view", "View clients and contacts"),
    _permission("contacts", "manage", "Create, update and delete clients"),
    _permission("contacts", "assign", "Reassign a client to another user"),
    _permission("properties", "view", "View property listings"),
    _permission("properties", "manage", "Create, update and delete listings"),
    _permission("properties", "assign", "Reassign a listing to another agent"),
    _permission("deals", "view", "View deals"),
    _permission("deals", "manage", "Create, update and delete deals"),
    _permission("deals", "approve", "Approve a deal or offer"),
    _permission("tasks", "view", "View tasks"),
    _permission("tasks", "manage", "Create, update and delete tasks"),
    _permission("activities", "view", "View the activity timeline"),
    _permission("activities", "manage", "Log and edit activities"),
    _permission("notes", "view", "View notes"),
    _permission("notes", "manage", "Write, edit and delete notes"),
    _permission("documents", "view", "View documents"),
    _permission("documents", "manage", "Upload and delete documents"),
    _permission("documents", "sign", "Request and complete signatures"),
    # --- automation ---
    #
    # Deliberately not granted to agents at any scope. A workflow acts on
    # records its author may not otherwise reach — that is the point of
    # automation — so authoring one is an administrative capability, not a
    # wider version of editing your own book. See docs/AUTOMATION.md §3.
    _permission("automations", "view", "View workflows and their run history"),
    _permission("automations", "manage", "Create, edit and publish workflows"),
    # --- insight ---
    _permission("reports", "view", "View reports and analytics"),
    _permission("reports", "export", "Export report data"),
    # --- administration ---
    _permission("users", "view", "View workspace members"),
    _permission("users", "manage", "Invite, update and deactivate users"),
    _permission("roles", "view", "View roles and permissions"),
    _permission("roles", "manage", "Create roles and change assignments"),
    _permission("settings", "view", "View workspace settings"),
    _permission("settings", "manage", "Change workspace settings"),
    _permission("audit", "view", "Read the audit log"),
    _permission("billing", "manage", "Manage plan and billing"),
    # --- AI (Phase 5; declared now so roles do not need re-seeding) ---
    _permission("ai", "use", "Use the AI assistant"),
    _permission("ai", "configure", "Change AI settings and prompts"),
)

PERMISSIONS_BY_KEY: dict[str, Permission] = {p.key: p for p in PERMISSIONS}
ALL_PERMISSION_KEYS: tuple[str, ...] = tuple(p.key for p in PERMISSIONS)


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    key: str
    name: str
    description: str
    #: permission key -> scope
    grants: dict[str, Scope]
    #: Owner is protected from deletion and from losing its own role.
    is_protected: bool = False


def _grant_all(scope: Scope, *keys: str) -> dict[str, Scope]:
    return dict.fromkeys(keys, scope)


def _every_permission(scope: Scope) -> dict[str, Scope]:
    return dict.fromkeys(ALL_PERMISSION_KEYS, scope)


# ------------------------------------------------------------ system roles

_AGENT_GRANTS: dict[str, Scope] = {
    # Own book of business.
    "leads.view": Scope.OWN,
    "leads.manage": Scope.OWN,
    "contacts.view": Scope.OWN,
    "contacts.manage": Scope.OWN,
    "deals.view": Scope.OWN,
    "deals.manage": Scope.OWN,
    "tasks.view": Scope.OWN,
    "tasks.manage": Scope.OWN,
    "activities.view": Scope.OWN,
    "activities.manage": Scope.OWN,
    "notes.view": Scope.OWN,
    "notes.manage": Scope.OWN,
    "documents.view": Scope.OWN,
    "documents.manage": Scope.OWN,
    # Listings are shared inventory: every agent sees all of them, but only
    # the listing agent edits. This asymmetry is the reason scope is separate
    # from permission.
    "properties.view": Scope.ALL,
    "properties.manage": Scope.OWN,
    # Own numbers only.
    "reports.view": Scope.OWN,
    "users.view": Scope.ALL,
    "settings.view": Scope.ALL,
    "ai.use": Scope.OWN,
}

_MANAGER_GRANTS: dict[str, Scope] = {
    **_AGENT_GRANTS,
    **_grant_all(
        Scope.TEAM,
        "leads.view",
        "leads.manage",
        "leads.assign",
        "contacts.view",
        "contacts.manage",
        "contacts.assign",
        "deals.view",
        "deals.manage",
        "deals.approve",
        "tasks.view",
        "tasks.manage",
        "activities.view",
        "activities.manage",
        "notes.view",
        "notes.manage",
        "documents.view",
        "documents.manage",
        "documents.sign",
        "reports.view",
        "reports.export",
    ),
    "properties.manage": Scope.TEAM,
    "properties.assign": Scope.TEAM,
    "ai.use": Scope.TEAM,
}

SYSTEM_ROLES: tuple[RoleDefinition, ...] = (
    RoleDefinition(
        key="owner",
        name="Owner",
        description="Full control of the workspace, including billing.",
        grants=_every_permission(Scope.ALL),
        is_protected=True,
    ),
    RoleDefinition(
        key="admin",
        name="Admin",
        description="Full operational control. Cannot manage billing.",
        grants={
            key: Scope.ALL for key in ALL_PERMISSION_KEYS if key != "billing.manage"
        },
    ),
    RoleDefinition(
        key="manager",
        name="Manager",
        description="Manages a team and everything that team owns.",
        grants=_MANAGER_GRANTS,
    ),
    RoleDefinition(
        key="agent",
        name="Agent",
        description="Works their own book of business.",
        grants=_AGENT_GRANTS,
    ),
)

SYSTEM_ROLES_BY_KEY: dict[str, RoleDefinition] = {r.key: r for r in SYSTEM_ROLES}

#: Assigned to the first user of a new workspace.
DEFAULT_OWNER_ROLE = "owner"
#: Assigned to an invited user when no role is specified.
DEFAULT_MEMBER_ROLE = "agent"


def validate_registry() -> None:
    """Fail fast if a role grants a permission that does not exist.

    Called at import time. A typo in a grant would otherwise become a silently
    missing permission — the role appears configured but the check never
    passes, which is confusing to debug and easy to mistake for a bug in the
    authorization layer.
    """
    for role in SYSTEM_ROLES:
        unknown = set(role.grants) - set(ALL_PERMISSION_KEYS)
        if unknown:
            raise ValueError(
                f"Role '{role.key}' grants unknown permissions: {sorted(unknown)}"
            )


validate_registry()
