"""The compliance controls registry — the closed set of what a tenant is measured against.

A registry rather than free strings, for the same reason the security event types
are: a control key that is not registered cannot be checked, evidenced, or
reported, so a typo is an error at the call site rather than a silent gap in a
compliance report. Each control names the framework it belongs to (the axis the
dashboard rolls up), a category, and whether it is mandatory (a failed mandatory
control fails the framework; an advisory one only warns).
"""

from __future__ import annotations

from dataclasses import dataclass

#: The frameworks controls roll up into.
FRAMEWORKS: tuple[str, ...] = ("gdpr", "soc2", "general")

#: A check's verdict for one control.
CHECK_STATUSES: tuple[str, ...] = ("pass", "warn", "fail", "not_applicable")


@dataclass(frozen=True, slots=True)
class ComplianceControl:
    key: str
    framework: str
    category: str
    title: str
    description: str
    #: A failed mandatory control fails its framework; an advisory one warns.
    mandatory: bool = True


def _c(
    key: str, framework: str, category: str, title: str, description: str,
    *, mandatory: bool = True,
) -> ComplianceControl:
    return ComplianceControl(
        key=key, framework=framework, category=category, title=title,
        description=description, mandatory=mandatory,
    )


COMPLIANCE_CONTROLS: dict[str, ComplianceControl] = {
    c.key: c
    for c in (
        _c("gdpr.ropa", "gdpr", "records",
           "Records of processing activities",
           "Maintain an Article 30 record of processing activities."),
        _c("gdpr.retention_policy", "gdpr", "retention",
           "Data retention policy",
           "Define retention windows so data is not kept longer than needed.",
           mandatory=False),
        _c("gdpr.dpo_contact", "gdpr", "governance",
           "Data protection contact",
           "Publish a data protection contact for data subjects.",
           mandatory=False),
        _c("gdpr.dsar_process", "gdpr", "rights",
           "Subject request handling",
           "Handle access and erasure requests within the SLA."),
        _c("gdpr.encryption_at_rest", "gdpr", "security",
           "Encryption at rest",
           "Encrypt sensitive data at rest."),
        _c("soc2.mfa_enforced", "soc2", "access",
           "MFA enforcement",
           "Require multi-factor authentication for the workspace.",
           mandatory=False),
        _c("soc2.access_restriction", "soc2", "access",
           "Network access restriction",
           "Restrict sign-in to an allowlisted network.",
           mandatory=False),
        _c("soc2.audit_logging", "soc2", "monitoring",
           "Audit logging",
           "Record security-relevant actions in an immutable log."),
        _c("general.sso", "general", "access",
           "Single sign-on",
           "Centralise identity through an SSO connection.",
           mandatory=False),
    )
}


def control(key: str) -> ComplianceControl:
    """Look up a registered control. Raises `KeyError` for an unknown one."""
    try:
        return COMPLIANCE_CONTROLS[key]
    except KeyError as exc:
        raise KeyError(f"Unknown compliance control '{key}'.") from exc


def controls_for_framework(framework: str) -> list[ComplianceControl]:
    return [c for c in COMPLIANCE_CONTROLS.values() if c.framework == framework]


__all__ = [
    "CHECK_STATUSES",
    "COMPLIANCE_CONTROLS",
    "FRAMEWORKS",
    "ComplianceControl",
    "control",
    "controls_for_framework",
]
