"""The enterprise governance layer (Phase 8.0).

Security policy, white-label branding, compliance (retention / legal hold /
GDPR), SSO + SCIM provisioning, and per-tenant feature management — the controls
a workspace administrator needs to run the product as an enterprise tenant.

The design keeps the *deterministic* rules (does this password satisfy the
policy? is this IP allowed? is this session still valid? which records are past
retention? does this identity provision, and as whom?) in `app.enterprise.policies`
as pure functions, separate from the services that persist and orchestrate them.
That is what makes the hard parts testable without a database and reviewable in
one place, and it is the same deterministic-first split the intelligence and
integration phases use.

Everything is tenant-scoped and RLS-FORCEd, and the layer reuses the existing
RBAC, audit, notification, worker, billing, and observability infrastructure
rather than re-implementing any of it.
"""

from __future__ import annotations

from app.enterprise.policies import (
    JitDecision,
    PasswordRules,
    SessionLimits,
    evaluate_password,
    ip_allowed,
    resolve_jit_identity,
    retention_cutoff,
    session_status,
)

__all__ = [
    "JitDecision",
    "PasswordRules",
    "SessionLimits",
    "evaluate_password",
    "ip_allowed",
    "resolve_jit_identity",
    "retention_cutoff",
    "session_status",
]
