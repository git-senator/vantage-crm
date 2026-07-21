"""Canonical audit action names.

Constants rather than free strings so a typo becomes an import error instead of
an entry nobody can find later. Grouped by the question an auditor asks.
"""

from __future__ import annotations

from typing import Final


class AuditAction:
    # --- authentication ---
    LOGIN_SUCCEEDED: Final = "auth.login.succeeded"
    LOGIN_FAILED: Final = "auth.login.failed"
    LOGOUT: Final = "auth.logout"
    PASSWORD_CHANGED: Final = "auth.password.changed"
    TOKEN_REFRESHED: Final = "auth.token.refreshed"
    #: Token theft or a client bug. Alerting hooks onto this.
    TOKEN_REUSE_DETECTED: Final = "auth.token.reuse_detected"
    ACCOUNT_LOCKED: Final = "auth.account.locked"

    # --- users ---
    USER_CREATED: Final = "user.created"
    USER_UPDATED: Final = "user.updated"
    USER_DEACTIVATED: Final = "user.deactivated"

    # --- authorization ---
    ROLE_ASSIGNED: Final = "role.assigned"
    ROLE_REVOKED: Final = "role.revoked"
    PERMISSION_DENIED: Final = "authz.denied"

    # --- organization ---
    ORGANIZATION_UPDATED: Final = "organization.updated"
    SETTINGS_CHANGED: Final = "organization.settings.changed"

    # --- data (Phase 2 onward) ---
    RECORD_CREATED: Final = "record.created"
    RECORD_UPDATED: Final = "record.updated"
    RECORD_DELETED: Final = "record.deleted"
    RECORD_EXPORTED: Final = "record.exported"


#: Actions that warrant alerting rather than just recording.
HIGH_SEVERITY_ACTIONS: frozenset[str] = frozenset(
    {
        AuditAction.TOKEN_REUSE_DETECTED,
        AuditAction.ROLE_ASSIGNED,
        AuditAction.ROLE_REVOKED,
        AuditAction.ACCOUNT_LOCKED,
        AuditAction.RECORD_EXPORTED,
    }
)
