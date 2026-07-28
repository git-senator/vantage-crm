"""App marketplace & plugin platform (Phase 9.0).

The properties that carry the milestone:

  * **The manifest is validated deterministically** — unknown capabilities,
    non-subscribable events, bad keys and bad versions are rejected at publish.
  * **The catalog is a marketplace** — first-party plugins are visible to every
    tenant, a tenant's private plugin is not, and keys are globally unique.
  * **Installation moves through an explicit lifecycle**, capabilities are a
    declared subset, and secret config is sealed at rest and never read back.
  * **Event subscriptions reuse the webhook vocabulary**, and the extension seam
    matches enabled installations and reuses RBAC to decide what may be invoked.
  * **A manifest's required feature is checked through the feature-flag service.**
  * **Tenant isolation holds**, and management is gated on `settings.manage`.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.core.secrets import is_token
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.models.plugin import PluginInstallation
from app.plugins.capabilities import capability, required_permissions
from app.plugins.lifecycle import LifecycleError, next_state
from app.plugins.manifest import secret_field_keys, validate_manifest
from app.schemas.plugin import ConfigureRequest, InstallRequest
from app.services.enterprise import FeatureService
from app.services.plugin import (
    MarketplaceDashboardService,
    PluginEventService,
    PluginExtensionService,
    PluginInstallationService,
    PluginRegistryService,
)
from app.services.rbac import AuthorizationContext
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


def _manifest(key: str = "acme_crm", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "key": key,
        "name": "Acme",
        "version": "1.2.0",
        "description": "An Acme plugin.",
        "publisher": "Acme Inc",
        "category": "integration",
        "capabilities": ["records.read", "events.subscribe"],
        "events": ["deal.created"],
        "config_schema": [
            {"key": "token", "label": "API token", "type": "secret", "required": True},
            {"key": "region", "label": "Region", "type": "string"},
        ],
    }
    base.update(over)
    return base


# ----------------------------------------------------- deterministic core


class TestManifestAndCapabilities:
    def test_valid_manifest(self) -> None:
        manifest = validate_manifest(_manifest())
        assert manifest.key == "acme_crm"
        assert secret_field_keys(manifest) == {"token"}

    def test_rejects_bad_manifests(self) -> None:
        with pytest.raises(AppError):
            validate_manifest(_manifest(version="1.0"))  # not semver
        with pytest.raises(AppError):
            validate_manifest(_manifest(key="Bad Key"))  # not snake_case
        with pytest.raises(AppError):
            validate_manifest(_manifest(capabilities=["nope.cap"]))
        with pytest.raises(AppError):
            validate_manifest(_manifest(events=["not.an.event"]))

    def test_capabilities_registry(self) -> None:
        assert capability("records.read").required_permission == "leads.view"
        with pytest.raises(KeyError):
            capability("nope")
        assert required_permissions(["events.subscribe"]) == {"settings.manage"}


class TestLifecycle:
    def test_transitions(self) -> None:
        assert next_state("installed", "enable") == "enabled"
        assert next_state("disabled", "enable") == "enabled"
        assert next_state("enabled", "disable") == "disabled"

    def test_illegal_transitions(self) -> None:
        with pytest.raises(LifecycleError):
            next_state("enabled", "enable")  # already enabled
        with pytest.raises(LifecycleError):
            next_state("installed", "disable")  # never enabled


# ------------------------------------------------------------ catalog


class TestCatalog:
    async def test_first_party_sync_is_global_and_idempotent(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        user = await make_user(db, organization, "cat@vantage.example")
        registry = PluginRegistryService(db, _auth(organization, user.id))
        count = await registry.sync_first_party(user)
        assert count == 3
        # Idempotent: a second sync refreshes, does not duplicate.
        await registry.sync_first_party(user)
        catalog = await registry.list_catalog()
        keys = [p.key for p in catalog]
        assert keys.count("slack") == 1 and "stripe" in keys

        # First-party plugins are visible to a different tenant too.
        them = await make_user(db, other_organization, "them@meridian.example")
        their_catalog = await PluginRegistryService(
            db, _auth(other_organization, them.id)
        ).list_catalog()
        assert {"slack", "stripe", "zapier"} <= {p.key for p in their_catalog}

    async def test_publish_and_private_isolation(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        user = await make_user(db, organization, "pub@vantage.example")
        registry = PluginRegistryService(db, _auth(organization, user.id))
        published = await registry.publish(user, _manifest(key="acme_private"))
        assert published.is_first_party is False

        # A different tenant does not see the private plugin...
        them = await make_user(db, other_organization, "them2@meridian.example")
        their_registry = PluginRegistryService(db, _auth(other_organization, them.id))
        assert "acme_private" not in {p.key for p in await their_registry.list_catalog()}

        # ...and the key is globally unique, so they cannot claim it either.
        with pytest.raises(AppError):
            await their_registry.publish(them, _manifest(key="acme_private"))

    async def test_publish_duplicate_same_tenant(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "dup@vantage.example")
        registry = PluginRegistryService(db, _auth(organization, user.id))
        await registry.publish(user, _manifest(key="acme_dup"))
        with pytest.raises(AppError):
            await registry.publish(user, _manifest(key="acme_dup"))


# ------------------------------------------------------------ installation


class TestInstallation:
    async def test_install_seals_secrets_and_subscribes(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "inst@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        await PluginRegistryService(db, auth).sync_first_party(user)
        slack = next(
            p for p in await PluginRegistryService(db, auth).list_catalog()
            if p.key == "slack"
        )
        installs = PluginInstallationService(db, auth, settings)
        installation = await installs.install(
            user,
            InstallRequest(
                plugin_id=slack.id,
                config={"channel": "#deals", "webhook_url": "https://hooks/x"},
            ),
        )
        assert installation.status == "installed"
        # The secret is sealed and never read back; the non-secret is visible.
        assert installation.config == {"channel": "#deals"}
        assert installation.secret_keys == ["webhook_url"]
        # Subscriptions were wired from the manifest events.
        assert set(installation.subscriptions) == {
            "lead.created", "deal.created", "deal.updated"
        }

        row = (
            await db.execute(
                select(PluginInstallation).where(
                    PluginInstallation.id == installation.id
                )
            )
        ).scalar_one()
        assert is_token(row.secrets["webhook_url"])

        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "plugin.installed")
            )
        ).scalars().all()
        assert len(audit) == 1

        # A second install of the same plugin is refused.
        with pytest.raises(AppError):
            await installs.install(user, InstallRequest(plugin_id=slack.id))

    async def test_lifecycle_and_uninstall(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "life@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        published = await PluginRegistryService(db, auth).publish(
            user, _manifest(key="life_plugin")
        )
        installs = PluginInstallationService(db, auth, settings)
        inst = await installs.install(user, InstallRequest(plugin_id=published.id))

        enabled = await installs.enable(user, inst.id)
        assert enabled.status == "enabled" and enabled.enabled_at is not None
        # Enabling an already-enabled installation is a precise refusal.
        with pytest.raises(LifecycleError):
            await installs.enable(user, inst.id)

        disabled = await installs.disable(user, inst.id)
        assert disabled.status == "disabled"

        await installs.uninstall(user, inst.id)
        with pytest.raises(NotFoundError):
            await installs.get(inst.id)

    async def test_capability_subset_enforced(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "cap@vantage.example")
        auth = _auth(organization, user.id)
        published = await PluginRegistryService(db, auth).publish(
            user, _manifest(key="cap_plugin")
        )
        installs = PluginInstallationService(db, auth, _settings())
        with pytest.raises(AppError):
            await installs.install(
                user,
                InstallRequest(
                    plugin_id=published.id,
                    granted_capabilities=["records.write"],  # not declared
                ),
            )

    async def test_required_feature_gate(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "feat@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        published = await PluginRegistryService(db, auth).publish(
            user, _manifest(key="beta_plugin", required_feature="beta_plugins")
        )
        installs = PluginInstallationService(db, auth, settings)
        with pytest.raises(AppError):
            await installs.install(user, InstallRequest(plugin_id=published.id))

        # Flip the feature flag through the existing feature service; now it installs.
        await FeatureService(db, auth, settings).set_flag(
            user, "beta_plugins", enabled=True, note=None
        )
        installation = await installs.install(
            user, InstallRequest(plugin_id=published.id)
        )
        assert installation.status == "installed"

    async def test_configure_seals_secret(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "conf@vantage.example")
        auth = _auth(organization, user.id)
        published = await PluginRegistryService(db, auth).publish(
            user, _manifest(key="conf_plugin")
        )
        installs = PluginInstallationService(db, auth, _settings())
        inst = await installs.install(user, InstallRequest(plugin_id=published.id))
        configured = await installs.configure(
            user, inst.id, ConfigureRequest(config={"token": "s3cr3t", "region": "eu"}).config
        )
        assert configured.config == {"region": "eu"}
        assert configured.secret_keys == ["token"]

    async def test_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "nom@vantage.example")
        installs = PluginInstallationService(
            db, _auth(organization, user.id, manage=False), _settings()
        )
        with pytest.raises(PermissionDeniedError):
            await installs.list_installations()


# ------------------------------------------------------ subscriptions & extension


class TestSubscriptionsAndExtension:
    async def test_subscription_rules_and_dispatch(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "sub@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        published = await PluginRegistryService(db, auth).publish(
            user, _manifest(key="sub_plugin", events=["deal.created"])
        )
        installs = PluginInstallationService(db, auth, settings)
        inst = await installs.install(user, InstallRequest(plugin_id=published.id))
        events = PluginEventService(db, auth)

        # Non-declared event is refused even though it is a valid webhook event.
        with pytest.raises(AppError):
            await events.subscribe(user, inst.id, "lead.created")
        # A non-subscribable event is refused too.
        with pytest.raises(AppError):
            await events.subscribe(user, inst.id, "not.an.event")

        extension = PluginExtensionService(db, auth)
        # Installed but not enabled -> no dispatch targets yet.
        assert await extension.dispatch_event("deal.created", {}) == 0

        await installs.enable(user, inst.id)
        assert await extension.dispatch_event("deal.created", {}) == 1
        assert len(await extension.subscribers("deal.created")) == 1
        enabled = await extension.enabled_installations(capability_key="records.read")
        assert len(enabled) == 1
        assert await extension.enabled_installations(capability_key="ai.invoke") == []

    def test_can_invoke_reuses_rbac(
        self, organization: Organization
    ) -> None:
        # settings.manage is granted -> events.subscribe (which maps to it) is
        # invocable; records.read (maps to leads.view) is not.
        extension = PluginExtensionService(None, _auth(organization, None))  # type: ignore[arg-type]
        assert extension.can_invoke("events.subscribe") is True
        assert extension.can_invoke("records.read") is False


# ------------------------------------------------------------ dashboard


class TestDashboard:
    async def test_dashboard_counts(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "dash@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        await PluginRegistryService(db, auth).sync_first_party(user)
        slack = next(
            p for p in await PluginRegistryService(db, auth).list_catalog()
            if p.key == "slack"
        )
        installs = PluginInstallationService(db, auth, settings)
        inst = await installs.install(user, InstallRequest(plugin_id=slack.id))
        await installs.enable(user, inst.id)

        dashboard = await MarketplaceDashboardService(db, auth).dashboard()
        assert dashboard.catalog_available >= 3
        assert dashboard.installed == 1 and dashboard.enabled == 1
        assert dashboard.subscriptions >= 1
