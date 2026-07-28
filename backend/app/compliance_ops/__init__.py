"""Enterprise compliance operations (Phase 8.3).

A compliance-ops layer *on top of* the Phase 8.0 governance and GDPR module, not
a replacement for it. It adds the operational apparatus a compliance officer
works with: a registry of controls, a deterministic checks framework that
evaluates the tenant's actual governance state against those controls, records of
processing activities (GDPR Art. 30), evidence collection, the DSAR workflow view
(over the existing data-request pipeline), a retention-execution hook (over the
existing retention sweep), and the audit dashboard that ties them together.

The judgements are deterministic and pure, in this package:

  * `registry` — the closed set of compliance controls and their frameworks.
  * `checks` — pure functions that turn a snapshot of governance state into a
    per-control pass/warn/fail, with evidence able to satisfy a control.

The services in `app.services.compliance_ops` gather that snapshot from the
existing governance repositories and reuse the GDPR data-request pipeline, the
retention sweep, the audit log, and RBAC — they never re-implement any of them.
"""

from __future__ import annotations

from app.compliance_ops.checks import (
    CheckContext,
    CheckResult,
    posture_for,
    run_checks,
)
from app.compliance_ops.registry import (
    COMPLIANCE_CONTROLS,
    ComplianceControl,
    control,
)

__all__ = [
    "COMPLIANCE_CONTROLS",
    "CheckContext",
    "CheckResult",
    "ComplianceControl",
    "control",
    "posture_for",
    "run_checks",
]
