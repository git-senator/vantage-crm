"""Enterprise security operations (Phase 8.2).

A security-ops layer *on top of* the Phase 8.0 governance, not a replacement for
it. It records security-relevant events, scores login risk, runs a small
detection framework over the activity, raises and tracks alerts, and builds the
dashboard the workspace's admins watch.

The design keeps the judgements deterministic and pure, in this package:

  * `registry` — the closed vocabulary of security event types and severities.
  * `risk` — login risk scoring and the deterministic enforcement gate.
  * `detectors` — pure functions that turn an activity context into alert
    proposals (brute force, new device, impossible travel, high-risk login).

The services in `app.services.security_ops` persist those judgements and reuse
the existing audit log, notification centre, MFA/session policy, and RLS — they
never re-implement authentication or rewrite a security module.
"""

from __future__ import annotations

from app.security_ops.detectors import (
    AlertProposal,
    DetectionContext,
    run_detectors,
)
from app.security_ops.registry import (
    SECURITY_EVENT_TYPES,
    SEVERITIES,
    event_type,
)
from app.security_ops.risk import (
    GateDecision,
    RiskAssessment,
    RiskSignals,
    derive_fingerprint,
    login_gate,
    score_login,
)

__all__ = [
    "SECURITY_EVENT_TYPES",
    "SEVERITIES",
    "AlertProposal",
    "DetectionContext",
    "GateDecision",
    "RiskAssessment",
    "RiskSignals",
    "derive_fingerprint",
    "event_type",
    "login_gate",
    "run_detectors",
    "score_login",
]
