"""Developer SDK bridge services.

The seam between the self-contained SDK contract (`app.sdk`) and the live plugin
platform (Phase 9.0). It serves the developer documentation generated from the
SDK definitions, validates a submitted manifest (reconciling the SDK's vocabulary
against the platform's authoritative registries to catch drift), and computes
diagnostics for an installed plugin.

Reuse is the rule: it reads the plugin catalog and installations through their
existing repositories (the runtime is untouched), checks a required feature
through the existing feature-flag service, maps capabilities to the existing RBAC
permissions, and writes to the audit log. The SDK package itself imports none of
this — the direction of dependency is one-way, from bridge to SDK.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.models.user import User
from app.plugins.capabilities import PLUGIN_CAPABILITIES
from app.repositories.plugin import (
    PluginEventSubscriptionRepository,
    PluginInstallationRepository,
    PluginRepository,
)
from app.schemas.sdk import (
    CapabilityDocRead,
    CompatibilityRead,
    DiagnosticCheckRead,
    DiagnosticReportRead,
    EventContractRead,
    HookDocRead,
    InterfaceDocRead,
    SdkVersionInfo,
    ValidationFindingRead,
    ValidationReportRead,
)
from app.sdk import diagnostics as sdk_diagnostics
from app.sdk import validation as sdk_validation
from app.sdk.capabilities import SDK_CAPABILITIES
from app.sdk.events import EVENT_CONTRACTS
from app.sdk.hooks import HOOK_POINTS
from app.sdk.version import MIN_SUPPORTED_MAJOR, SDK_VERSION, compatibility, is_compatible
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext
from app.webhooks.events import WEBHOOK_EVENT_TYPES

MANAGE_PERMISSION = "settings.manage"

#: The interfaces a plugin implements or is injected with — the SDK surface, as
#: documentation. Static (name, kind, methods) because Protocols do not reflect
#: cleanly.
_INTERFACE_DOCS: tuple[tuple[str, str, list[str]], ...] = (
    ("Extension", "implement", ["key", "version"]),
    ("EventHandler", "implement", ["events", "handle"]),
    ("RecordsClient", "inject", ["get", "create", "update"]),
    ("MessagingClient", "inject", ["send"]),
    ("AiClient", "inject", ["complete"]),
)


class SdkDocsService:
    """The developer documentation surface — available to any authenticated
    member, generated from the SDK definitions rather than hand-written."""

    def version_info(self) -> SdkVersionInfo:
        return SdkVersionInfo(
            sdk_version=SDK_VERSION,
            min_supported_major=MIN_SUPPORTED_MAJOR,
            events=sorted(EVENT_CONTRACTS),
            capabilities=sorted(SDK_CAPABILITIES),
            hooks=sorted(HOOK_POINTS),
        )

    def event_docs(self) -> list[EventContractRead]:
        docs: list[EventContractRead] = []
        for contract in EVENT_CONTRACTS.values():
            fields = [
                {"name": name, "type": _type_label(info.annotation)}
                for name, info in contract.payload_model.model_fields.items()
            ]
            docs.append(
                EventContractRead(
                    event_type=contract.event_type,
                    entity=contract.entity,
                    description=contract.description,
                    fields=fields,
                )
            )
        return docs

    def capability_docs(self) -> list[CapabilityDocRead]:
        docs: list[CapabilityDocRead] = []
        for cap in SDK_CAPABILITIES.values():
            platform = PLUGIN_CAPABILITIES.get(cap.key)
            docs.append(
                CapabilityDocRead(
                    key=cap.key,
                    title=cap.title,
                    description=cap.description,
                    required_permission=(
                        platform.required_permission if platform else "settings.manage"
                    ),
                )
            )
        return docs

    def hook_docs(self) -> list[HookDocRead]:
        return [
            HookDocRead(name=h.name, description=h.description, event_type=h.event_type)
            for h in HOOK_POINTS.values()
        ]

    def interface_docs(self) -> list[InterfaceDocRead]:
        return [
            InterfaceDocRead(name=name, kind=kind, methods=methods)
            for name, kind, methods in _INTERFACE_DOCS
        ]

    def compatibility(self, plugin_version: str) -> CompatibilityRead:
        result = compatibility(plugin_version)
        return CompatibilityRead(
            plugin_version=result.plugin_version,
            sdk_version=result.sdk_version,
            compatible=result.compatible,
            reason=result.reason,
        )


def _type_label(annotation: object) -> str:
    text = str(annotation)
    return text.replace("typing.", "")


class SdkValidationService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.audit = AuditService(session)

    async def validate(
        self, actor: User, manifest: dict[str, object]
    ) -> ValidationReportRead:
        self.auth.require(MANAGE_PERMISSION)
        report = sdk_validation.validate_manifest(
            manifest, strict=self.settings.SDK_STRICT_VALIDATION
        )
        findings = [
            ValidationFindingRead(level=f.level, code=f.code, message=f.message)
            for f in report.findings
        ]

        # Reconcile the SDK vocabulary against the platform's authoritative
        # registries — a drift between the two is a real error, not a warning.
        declared_caps = manifest.get("capabilities")
        for cap in declared_caps if isinstance(declared_caps, list) else []:
            if str(cap) not in PLUGIN_CAPABILITIES:
                findings.append(
                    ValidationFindingRead(
                        level="error",
                        code="platform_unknown_capability",
                        message=f"Capability '{cap}' is not offered by this platform.",
                    )
                )
        declared_events = manifest.get("events")
        for event in declared_events if isinstance(declared_events, list) else []:
            if str(event) not in WEBHOOK_EVENT_TYPES:
                findings.append(
                    ValidationFindingRead(
                        level="error",
                        code="platform_unknown_event",
                        message=f"Event '{event}' is not emitted by this platform.",
                    )
                )

        error_count = sum(1 for f in findings if f.level == "error")
        warning_count = sum(1 for f in findings if f.level == "warning")
        ok = error_count == 0 and not (
            self.settings.SDK_STRICT_VALIDATION and warning_count
        )

        await self.audit.record(
            action=AuditAction.SDK_PLUGIN_VALIDATED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="plugin_manifest",
            entity_id=None,
            metadata={"key": manifest.get("key"), "ok": ok, "errors": error_count},
        )
        return ValidationReportRead(
            ok=ok,
            sdk_version=report.sdk_version,
            declared_version=report.declared_version,
            error_count=error_count,
            warning_count=warning_count,
            findings=findings,
        )


class PluginDiagnosticsService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.installs = PluginInstallationRepository(session)
        self.plugins = PluginRepository(session)
        self.subs = PluginEventSubscriptionRepository(session)

    async def diagnose(self, installation_id: UUID) -> DiagnosticReportRead:
        self.auth.require(MANAGE_PERMISSION)
        installation = await self.installs.get(
            installation_id, self.auth.organization_id
        )
        if installation is None:
            raise NotFoundError("Installation not found.")
        plugin = await self.plugins.get_visible(
            installation.plugin_id, self.auth.organization_id
        )
        if plugin is None:
            raise NotFoundError("Plugin not found.")

        subscriptions = {
            s.event_type
            for s in await self.subs.list_for_installation(
                self.auth.organization_id, installation_id
            )
        }
        required_config = {
            f["key"]
            for f in plugin.config_schema
            if isinstance(f, dict) and f.get("required")
        }
        config_keys_set = set(installation.config) | set(installation.secrets)

        declared_sdk = plugin.manifest.get("sdk_version")
        declared_sdk = str(declared_sdk) if declared_sdk else None
        sdk_ok = is_compatible(declared_sdk) if declared_sdk else True

        feature_enabled = True
        if plugin.required_feature:
            from app.services.enterprise import FeatureService

            feature_enabled = await FeatureService(
                self.session, self.auth, self.settings
            ).has_feature(plugin.required_feature)

        snapshot = sdk_diagnostics.DiagnosticInput(
            status=installation.status,
            sdk_compatible=sdk_ok,
            declared_sdk_version=declared_sdk,
            granted_capabilities=frozenset(installation.granted_capabilities),
            manifest_capabilities=frozenset(plugin.capabilities),
            config_keys_set=frozenset(config_keys_set),
            required_config_keys=frozenset(required_config),
            subscriptions=frozenset(subscriptions),
            manifest_events=frozenset(plugin.event_types),
            required_feature=plugin.required_feature,
            feature_enabled=feature_enabled,
        )
        report = sdk_diagnostics.diagnose(snapshot)
        return DiagnosticReportRead(
            installation_id=installation.id,
            plugin_key=plugin.key,
            health=report.health,
            checks=[
                DiagnosticCheckRead(name=c.name, status=c.status, detail=c.detail)
                for c in report.checks
            ],
        )


__all__ = [
    "MANAGE_PERMISSION",
    "PluginDiagnosticsService",
    "SdkDocsService",
    "SdkValidationService",
]
