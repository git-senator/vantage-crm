"""The certification-framework registry — the closed set a tenant can track.

A registry rather than free strings, for the same reason the compliance controls
and security event types are: a framework key that is not registered cannot be
tracked or surfaced on the customer-facing overview, so a typo is an error at the
call site instead of a certification nobody can find. Each framework names the
authority that issues it and whether it is time-bounded (most attestations expire
and must be renewed; a self-assessment does not).
"""

from __future__ import annotations

from dataclasses import dataclass

#: The lifecycle a tracked certification moves through.
CERTIFICATION_STATUSES: tuple[str, ...] = (
    "not_started",
    "in_progress",
    "certified",
    "expired",
)


@dataclass(frozen=True, slots=True)
class CertificationFramework:
    key: str
    name: str
    authority: str
    description: str
    #: Whether an attestation of this framework carries an expiry date. A
    #: self-assessment (GDPR, CCPA) does not; a third-party audit (SOC 2, ISO)
    #: does, and a lapsed one is a finding.
    time_bounded: bool = True


def _f(
    key: str, name: str, authority: str, description: str, *, time_bounded: bool = True
) -> CertificationFramework:
    return CertificationFramework(
        key=key, name=name, authority=authority,
        description=description, time_bounded=time_bounded,
    )


CERTIFICATION_FRAMEWORKS: dict[str, CertificationFramework] = {
    f.key: f
    for f in (
        _f("soc2_type1", "SOC 2 Type I", "AICPA",
           "Point-in-time attestation of security controls design."),
        _f("soc2_type2", "SOC 2 Type II", "AICPA",
           "Attestation of security controls operating over a period."),
        _f("iso_27001", "ISO/IEC 27001", "ISO",
           "Certification of an information security management system."),
        _f("iso_27701", "ISO/IEC 27701", "ISO",
           "Privacy information management extension to ISO 27001."),
        _f("pci_dss", "PCI DSS", "PCI SSC",
           "Payment card data security standard compliance."),
        _f("hipaa", "HIPAA", "HHS",
           "Attestation of safeguards for protected health information."),
        _f("gdpr", "GDPR", "EU",
           "Self-assessed compliance with EU data protection law.",
           time_bounded=False),
        _f("ccpa", "CCPA/CPRA", "California",
           "Self-assessed compliance with California privacy law.",
           time_bounded=False),
    )
}


def certification_framework(key: str) -> CertificationFramework:
    """Look up a registered framework. Raises `KeyError` for an unknown one."""
    try:
        return CERTIFICATION_FRAMEWORKS[key]
    except KeyError as exc:
        raise KeyError(f"Unknown certification framework '{key}'.") from exc


__all__ = [
    "CERTIFICATION_FRAMEWORKS",
    "CERTIFICATION_STATUSES",
    "CertificationFramework",
    "certification_framework",
]
