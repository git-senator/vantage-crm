"""The compliance checks framework — deterministic, evidence-aware.

Each control has a pure check: a snapshot of the tenant's governance state in, a
pass/warn/fail out. The framework runs every registered control's check over one
context, then applies two rules that make the result honest:

  * **Evidence can satisfy a control.** A control the tenant has attached evidence
    for is treated as met even if the automated signal is absent — some controls
    are procedural and cannot be proven by a database flag, which is exactly what
    evidence collection is for. Evidence only ever *upgrades* a verdict.
  * **Mandatory vs advisory drives the rollup.** A failed mandatory control fails
    its framework; an advisory one only warns. So the posture reflects severity,
    not a raw count.

All pure: the same context always yields the same report, which is what makes a
compliance dashboard defensible rather than a guess.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.compliance_ops.registry import COMPLIANCE_CONTROLS, ComplianceControl


@dataclass(frozen=True, slots=True)
class CheckContext:
    """A snapshot of the tenant's governance state, gathered by the service from
    the existing repositories. Pure inputs so the checks stay I/O-free."""

    has_retention_policy: bool = False
    has_dpo_contact: bool = False
    mfa_required: bool = False
    ip_enforcement: bool = False
    sso_enabled: bool = False
    encryption_at_rest: bool = False
    audit_logging: bool = True  # always on in this system
    ropa_count: int = 0
    overdue_dsar_count: int = 0
    #: Control keys that have at least one evidence record attached.
    evidenced_controls: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class CheckResult:
    control: ComplianceControl
    status: str
    summary: str
    #: True when evidence upgraded the automated verdict.
    evidenced: bool = False


def _verdict(ok: bool, *, warn_only: bool = False) -> str:
    if ok:
        return "pass"
    return "warn" if warn_only else "fail"


# One check per control. Each is a pure function of the context.

def _ropa(ctx: CheckContext) -> tuple[str, str]:
    ok = ctx.ropa_count > 0
    return _verdict(ok), (
        f"{ctx.ropa_count} processing activities recorded"
        if ok
        else "No records of processing activities"
    )


def _retention(ctx: CheckContext) -> tuple[str, str]:
    return (
        _verdict(ctx.has_retention_policy, warn_only=True),
        "Retention policy configured" if ctx.has_retention_policy else "No retention policy",
    )


def _dpo(ctx: CheckContext) -> tuple[str, str]:
    return (
        _verdict(ctx.has_dpo_contact, warn_only=True),
        "Data protection contact published" if ctx.has_dpo_contact else "No DPO contact",
    )


def _dsar(ctx: CheckContext) -> tuple[str, str]:
    ok = ctx.overdue_dsar_count == 0
    return _verdict(ok), (
        "No overdue subject requests"
        if ok
        else f"{ctx.overdue_dsar_count} subject requests past SLA"
    )


def _encryption(ctx: CheckContext) -> tuple[str, str]:
    return (
        _verdict(ctx.encryption_at_rest),
        "Encryption at rest configured" if ctx.encryption_at_rest else "Encryption not configured",
    )


def _mfa(ctx: CheckContext) -> tuple[str, str]:
    return (
        _verdict(ctx.mfa_required, warn_only=True),
        "MFA enforced" if ctx.mfa_required else "MFA not enforced",
    )


def _access(ctx: CheckContext) -> tuple[str, str]:
    return (
        _verdict(ctx.ip_enforcement, warn_only=True),
        "IP allowlist enforced" if ctx.ip_enforcement else "No network restriction",
    )


def _audit(ctx: CheckContext) -> tuple[str, str]:
    return (
        _verdict(ctx.audit_logging),
        "Audit logging active" if ctx.audit_logging else "Audit logging inactive",
    )


def _sso(ctx: CheckContext) -> tuple[str, str]:
    return (
        _verdict(ctx.sso_enabled, warn_only=True),
        "SSO enabled" if ctx.sso_enabled else "SSO not configured",
    )


_CHECKS: dict[str, Callable[[CheckContext], tuple[str, str]]] = {
    "gdpr.ropa": _ropa,
    "gdpr.retention_policy": _retention,
    "gdpr.dpo_contact": _dpo,
    "gdpr.dsar_process": _dsar,
    "gdpr.encryption_at_rest": _encryption,
    "soc2.mfa_enforced": _mfa,
    "soc2.access_restriction": _access,
    "soc2.audit_logging": _audit,
    "general.sso": _sso,
}


def run_checks(ctx: CheckContext) -> list[CheckResult]:
    """Evaluate every registered control against the context, evidence-aware."""
    results: list[CheckResult] = []
    for key, ctrl in COMPLIANCE_CONTROLS.items():
        check = _CHECKS.get(key)
        if check is None:
            results.append(CheckResult(ctrl, "not_applicable", "No automated check"))
            continue
        status, summary = check(ctx)
        evidenced = False
        if status in ("warn", "fail") and key in ctx.evidenced_controls:
            status, summary, evidenced = "pass", f"{summary} (satisfied by evidence)", True
        results.append(CheckResult(ctrl, status, summary, evidenced=evidenced))
    return results


@dataclass(frozen=True, slots=True)
class Posture:
    status: str
    passed: int
    warned: int
    failed: int
    total: int
    frameworks: dict[str, str] = field(default_factory=dict)


def posture_for(results: list[CheckResult]) -> Posture:
    """Roll per-control results up into an overall and per-framework posture.

    A mandatory failure fails; otherwise any warn (or an advisory failure)
    warns; otherwise pass. `not_applicable` controls do not count against it.
    """
    passed = sum(1 for r in results if r.status == "pass")
    warned = sum(1 for r in results if r.status == "warn")
    failed = sum(1 for r in results if r.status == "fail")

    by_framework: dict[str, list[CheckResult]] = {}
    for result in results:
        by_framework.setdefault(result.control.framework, []).append(result)

    return Posture(
        status=_rollup(results),
        passed=passed,
        warned=warned,
        failed=failed,
        total=sum(1 for r in results if r.status != "not_applicable"),
        frameworks={fw: _rollup(rs) for fw, rs in by_framework.items()},
    )


def _rollup(results: list[CheckResult]) -> str:
    has_mandatory_fail = any(
        r.status == "fail" and r.control.mandatory for r in results
    )
    if has_mandatory_fail:
        return "fail"
    if any(r.status in ("warn", "fail") for r in results):
        return "warn"
    return "compliant"


__all__ = [
    "CheckContext",
    "CheckResult",
    "Posture",
    "posture_for",
    "run_checks",
]
