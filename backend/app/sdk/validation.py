"""The SDK validation framework — is this manifest a valid plugin?

A deterministic, total check of a manifest against the SDK contract, returning a
structured report rather than raising on the first problem: a developer wants
every issue at once. It checks the declared SDK version is one this platform can
run, the capabilities and events are all known to the SDK, the config schema is
well-formed, and a few coherence rules (subscribing to events needs the
``events.subscribe`` capability).

Pure and self-contained — it validates against the SDK's own vocabularies, so it
runs anywhere the SDK does, with no CRM import. The backend bridge additionally
reconciles against the live platform registries to catch drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.sdk.capabilities import is_known_capability
from app.sdk.events import is_known_event
from app.sdk.version import SDK_VERSION, compatibility

_VALID_FIELD_TYPES = frozenset({"string", "number", "boolean", "secret"})


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    level: str  # "error" | "warning"
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    ok: bool
    sdk_version: str
    declared_version: str | None
    findings: list[ValidationFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.level == "warning"]


def validate_manifest(
    manifest: Mapping[str, Any], *, strict: bool = False
) -> ValidationReport:
    """Validate a manifest. In ``strict`` mode a warning also fails the report."""
    findings: list[ValidationFinding] = []

    def error(code: str, message: str) -> None:
        findings.append(ValidationFinding("error", code, message))

    def warn(code: str, message: str) -> None:
        findings.append(ValidationFinding("warning", code, message))

    for required in ("key", "name", "version"):
        if not manifest.get(required):
            error("missing_field", f"Manifest is missing '{required}'.")

    declared = manifest.get("sdk_version")
    if declared is None:
        warn("no_sdk_version",
             f"No sdk_version declared; assuming {SDK_VERSION}.")
    else:
        result = compatibility(str(declared))
        if not result.compatible:
            error("incompatible_sdk", result.reason)

    capabilities = manifest.get("capabilities") or []
    for cap in capabilities:
        if not is_known_capability(str(cap)):
            error("unknown_capability", f"Unknown capability '{cap}'.")

    events = manifest.get("events") or []
    for event in events:
        if not is_known_event(str(event)):
            error("unknown_event", f"Unknown or non-subscribable event '{event}'.")
    if events and "events.subscribe" not in capabilities:
        warn("events_need_capability",
             "Declares events but not the 'events.subscribe' capability.")

    for entry in manifest.get("config_schema") or []:
        if not isinstance(entry, Mapping) or not entry.get("key"):
            error("bad_config_field", "A config_schema entry has no key.")
            continue
        field_type = entry.get("type", "string")
        if field_type not in _VALID_FIELD_TYPES:
            error("bad_config_type",
                  f"Config field '{entry.get('key')}' has bad type '{field_type}'.")

    has_error = any(f.level == "error" for f in findings)
    has_warning = any(f.level == "warning" for f in findings)
    ok = not has_error and not (strict and has_warning)
    return ValidationReport(
        ok=ok,
        sdk_version=SDK_VERSION,
        declared_version=None if declared is None else str(declared),
        findings=findings,
    )


__all__ = [
    "ValidationFinding",
    "ValidationReport",
    "validate_manifest",
]
