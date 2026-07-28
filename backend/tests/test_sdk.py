"""Developer SDK & extension framework (Phase 9.1).

The properties that carry the milestone:

  * **The SDK package is self-contained** — it imports no internal CRM module, so
    a third party can build against it without depending on the core.
  * **Its vocabularies mirror the platform** — capabilities and event contracts
    stay aligned with the plugin platform and the webhook events (a drift is a
    test failure here).
  * **The compatibility layer is deterministic** — a major mismatch or a
    too-new minor is refused.
  * **Event payloads are typed**, the context enforces capabilities, and the hook
    registry rejects unknown hooks.
  * **Validation reports every problem at once**; **diagnostics roll an
    installation's checks up to a health**, reusing the live plugin platform and
    feature flags. Validation is audited.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import PermissionDeniedError
from app.core.permissions import Scope
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.plugins.capabilities import PLUGIN_CAPABILITIES
from app.schemas.plugin import ConfigureRequest, InstallRequest
from app.sdk.capabilities import SDK_CAPABILITIES
from app.sdk.context import PluginContext, PluginServices
from app.sdk.diagnostics import DiagnosticInput, diagnose
from app.sdk.errors import SdkPermissionError, SdkValidationError
from app.sdk.events import EVENT_CONTRACTS, event_contract, parse_event
from app.sdk.hooks import HookRegistry, hook_point
from app.sdk.validation import validate_manifest
from app.sdk.version import SDK_VERSION, compatibility, is_compatible, parse_version
from app.services.plugin import PluginInstallationService, PluginRegistryService
from app.services.rbac import AuthorizationContext
from app.services.sdk import PluginDiagnosticsService, SdkDocsService, SdkValidationService
from app.webhooks.events import WEBHOOK_EVENT_TYPES
from tests.conftest import make_user

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "JWT_SECRET": SecretStr(TEST_JWT_SECRET),
        "ENVIRONMENT": "test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _auth(organization: Organization, user_id, manage: bool = True) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    grants = {"settings.manage": Scope.ALL} if manage else {"leads.view": Scope.OWN}
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants=grants,
    )


def _manifest(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "key": "good_plugin",
        "name": "Good",
        "version": "1.0.0",
        "description": "A good plugin.",
        "publisher": "Dev",
        "category": "integration",
        "sdk_version": "1.0.0",
        "capabilities": ["events.subscribe"],
        "events": ["deal.created"],
        "config_schema": [
            {"key": "token", "label": "Token", "type": "secret", "required": True},
        ],
    }
    base.update(over)
    return base


# --------------------------------------------- self-containment & alignment


class TestContract:
    def test_sdk_imports_no_internal_modules(self) -> None:
        """The SDK must not import any internal CRM package (only `app.sdk`
        itself, the stdlib, and Pydantic). Parsed from the AST, so docstrings
        that merely name a forbidden module do not trip the check."""
        sdk_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "sdk"

        def _modules(tree: ast.AST) -> set[str]:
            names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module)
            return names

        for path in sdk_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for module in _modules(tree):
                if module.startswith("app.") and not module.startswith("app.sdk"):
                    pytest.fail(f"{path.name} imports internal module '{module}'")

    def test_capabilities_mirror_platform(self) -> None:
        assert set(SDK_CAPABILITIES) == set(PLUGIN_CAPABILITIES)

    def test_events_mirror_webhook_vocabulary(self) -> None:
        assert set(EVENT_CONTRACTS) == set(WEBHOOK_EVENT_TYPES)


# --------------------------------------------- version / compatibility


class TestVersion:
    def test_parse(self) -> None:
        assert parse_version("1.2.3") == (1, 2, 3)
        assert parse_version("1.2") == (1, 2, 0)
        with pytest.raises(ValueError):
            parse_version("nope")

    def test_compatibility(self) -> None:
        assert is_compatible(SDK_VERSION) is True
        assert is_compatible("2.0.0") is False  # major mismatch
        assert is_compatible("1.9.0") is False  # newer minor than platform
        assert is_compatible("1.0.5") is True   # patch never matters
        assert compatibility("bad").compatible is False


# --------------------------------------------- events / context / hooks


class TestEventsContextHooks:
    def test_parse_event_is_typed(self) -> None:
        event = parse_event(
            {
                "id": "d1", "event": "deal.created", "event_id": "e1",
                "created_at": "now", "organization_id": "o1",
                "data": {"id": "deal-1", "value": 1000, "title": "Big"},
            }
        )
        assert event.event == "deal.created"
        assert event.data["value"] == 1000
        with pytest.raises(SdkValidationError):
            parse_event({"event": "not.a.thing", "data": {}})
        with pytest.raises(KeyError):
            event_contract("nope")

    def test_context_capability_gate(self) -> None:
        ctx = PluginContext(
            organization_id="o", installation_id="i", plugin_key="p",
            sdk_version=SDK_VERSION, capabilities=frozenset({"records.read"}),
            services=PluginServices(),
        )
        assert ctx.has("records.read") is True
        ctx.require("records.read")
        with pytest.raises(SdkPermissionError):
            ctx.require("records.write")

    def test_hook_registry(self) -> None:
        registry = HookRegistry()

        async def handler(**_: object) -> None:
            return None

        registry.register("event.received", handler)
        assert len(registry.handlers("event.received")) == 1
        assert "event.received" in registry.hooks()
        with pytest.raises(KeyError):
            registry.register("nope", handler)
        with pytest.raises(KeyError):
            hook_point("nope")


# --------------------------------------------- validation framework


class TestValidation:
    def test_valid_manifest(self) -> None:
        report = validate_manifest(_manifest())
        assert report.ok is True and report.errors == []

    def test_collects_every_error(self) -> None:
        report = validate_manifest(
            _manifest(
                capabilities=["nope.cap"],
                events=["not.an.event"],
                sdk_version="2.0.0",
            )
        )
        codes = {f.code for f in report.errors}
        assert {"unknown_capability", "unknown_event", "incompatible_sdk"} <= codes
        assert report.ok is False

    def test_missing_sdk_version_warns(self) -> None:
        manifest = _manifest()
        del manifest["sdk_version"]
        report = validate_manifest(manifest)
        assert any(f.code == "no_sdk_version" for f in report.warnings)
        assert report.ok is True  # a warning alone does not fail

    def test_strict_mode_fails_on_warning(self) -> None:
        manifest = _manifest(events=["deal.created"], capabilities=[])
        report = validate_manifest(manifest, strict=True)
        # events without events.subscribe warns; strict turns that into a failure.
        assert report.ok is False


# --------------------------------------------- diagnostics core


class TestDiagnosticsCore:
    def _base(self, **over: object) -> DiagnosticInput:
        base: dict[str, object] = {
            "status": "enabled",
            "sdk_compatible": True,
            "declared_sdk_version": "1.0.0",
            "granted_capabilities": frozenset({"events.subscribe"}),
            "manifest_capabilities": frozenset({"events.subscribe"}),
            "config_keys_set": frozenset({"token"}),
            "required_config_keys": frozenset({"token"}),
            "subscriptions": frozenset({"deal.created"}),
            "manifest_events": frozenset({"deal.created"}),
            "required_feature": None,
            "feature_enabled": True,
        }
        base.update(over)
        return DiagnosticInput(**base)  # type: ignore[arg-type]

    def test_healthy(self) -> None:
        assert diagnose(self._base()).health == "healthy"

    def test_missing_config_is_unhealthy(self) -> None:
        report = diagnose(self._base(config_keys_set=frozenset()))
        assert report.health == "unhealthy"
        assert any(c.name == "config" and c.status == "fail" for c in report.checks)

    def test_unsubscribed_is_degraded(self) -> None:
        report = diagnose(self._base(subscriptions=frozenset()))
        assert report.health == "degraded"


# --------------------------------------------- backend bridge (docs)


class TestDocsService:
    def test_version_and_docs(self) -> None:
        docs = SdkDocsService()
        info = docs.version_info()
        assert info.sdk_version == SDK_VERSION
        assert "deal.created" in info.events
        assert docs.event_docs() and docs.capability_docs()
        assert docs.hook_docs() and docs.interface_docs()
        assert docs.compatibility("2.0.0").compatible is False


# --------------------------------------------- backend bridge (validate/diagnose)


class TestBridgeServices:
    async def test_validate_records_audit(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "val@vantage.example")
        auth = _auth(organization, user.id)
        service = SdkValidationService(db, auth, _settings())

        good = await service.validate(user, _manifest())
        assert good.ok is True

        bad = await service.validate(user, _manifest(capabilities=["nope.cap"]))
        assert bad.ok is False and bad.error_count >= 1

        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "sdk.plugin.validated")
            )
        ).scalars().all()
        assert len(audit) == 2

    async def test_validate_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "val2@vantage.example")
        service = SdkValidationService(
            db, _auth(organization, user.id, manage=False), _settings()
        )
        with pytest.raises(PermissionDeniedError):
            await service.validate(user, _manifest())

    async def test_diagnostics_over_installation(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "diag@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        published = await PluginRegistryService(db, auth).publish(user, _manifest())
        installs = PluginInstallationService(db, auth, settings)
        inst = await installs.install(user, InstallRequest(plugin_id=published.id))

        diag = PluginDiagnosticsService(db, auth, settings)
        # Installed but required 'token' config is unset -> a failing config check.
        before = await diag.diagnose(inst.id)
        assert before.health == "unhealthy"
        assert any(c.name == "config" and c.status == "fail" for c in before.checks)

        # Configure the secret and enable -> the config and lifecycle checks pass.
        await installs.configure(
            user, inst.id, ConfigureRequest(config={"token": "abc"}).config
        )
        await installs.enable(user, inst.id)
        after = await diag.diagnose(inst.id)
        assert after.health in ("healthy", "degraded")
        assert all(
            c.status != "fail" for c in after.checks if c.name in ("config", "lifecycle")
        )
