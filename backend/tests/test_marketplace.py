"""Integration marketplace (Phase 9.2).

The properties that carry the milestone:

  * **The vocabularies are closed and derived** — categories and certification
    tiers are a fixed set, and a listing's certification is computed from evidence,
    not hand-set.
  * **Every curated template is a valid plugin** — each template's manifest passes
    the platform's own validation, and its capabilities and events stay inside the
    platform's vocabularies (a drift is a failure here).
  * **The registry is a marketplace** — curated listings are visible to every
    tenant, discovery filters and searches, and the sync is idempotent.
  * **Installation reuses the plugin platform** — installing a listing installs its
    plugin, sealing secrets and wiring subscriptions; the marketplace never
    restates the lifecycle. It is audited.
  * **Health folds the plugin diagnostics into one rating**, and management is
    gated on `settings.manage`.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import PermissionDeniedError
from app.core.permissions import Scope
from app.core.secrets import is_token
from app.marketplace.categories import INTEGRATION_CATEGORIES, is_known_category
from app.marketplace.certification import (
    CertificationSignals,
    assess_certification,
    certification_rank,
    is_certified,
)
from app.marketplace.connections import (
    ConnectionReadiness,
    auth_spec,
    connection_state,
)
from app.marketplace.health import (
    IntegrationHealthSignals,
    integration_health,
    summarize_health,
)
from app.marketplace.templates import INTEGRATION_TEMPLATES, integration_template
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.models.plugin import PluginInstallation
from app.plugins.capabilities import PLUGIN_CAPABILITIES
from app.plugins.manifest import PLUGIN_CATEGORIES, validate_manifest
from app.schemas.marketplace import InstallListingRequest
from app.services.marketplace import (
    IntegrationHealthService,
    IntegrationInstallationService,
    IntegrationRegistryService,
)
from app.services.rbac import AuthorizationContext
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


# --------------------------------------------------- vocabularies (pure)


class TestVocabularies:
    def test_categories_closed(self) -> None:
        assert is_known_category("payments") and not is_known_category("nope")
        assert "esignature" in INTEGRATION_CATEGORIES

    def test_certification_ladder(self) -> None:
        assert certification_rank("community") < certification_rank("certified")
        assert is_certified("certified") and is_certified("official")
        assert not is_certified("verified")

    def test_certification_is_derived(self) -> None:
        def signals(**over: bool) -> CertificationSignals:
            base = {
                "has_publisher": True,
                "manifest_valid": True,
                "capabilities_reviewed": True,
                "security_reviewed": True,
                "is_first_party": False,
            }
            base.update(over)
            return CertificationSignals(**base)  # type: ignore[arg-type]

        assert assess_certification(signals(is_first_party=True)) == "official"
        assert assess_certification(signals()) == "certified"
        assert assess_certification(signals(security_reviewed=False)) == "verified"
        assert assess_certification(signals(has_publisher=False)) == "community"
        assert assess_certification(signals(manifest_valid=False)) == "community"

    def test_auth_spec_validates_method(self) -> None:
        assert auth_spec("oauth2", scopes=("a",)).method == "oauth2"
        with pytest.raises(ValueError):
            auth_spec("carrier_pigeon")

    def test_connection_state(self) -> None:
        # A pasted-secret integration with its secret set is connected.
        ready = ConnectionReadiness(
            method="api_key",
            required_secret_keys=frozenset({"api_key"}),
            present_secret_keys=frozenset({"api_key"}),
        )
        assert connection_state(ready) == "connected"
        # Missing the secret -> not connected.
        missing = ConnectionReadiness(
            method="api_key",
            required_secret_keys=frozenset({"api_key"}),
            present_secret_keys=frozenset(),
        )
        assert connection_state(missing) == "not_connected"
        # An OAuth connection that lapsed -> expired.
        lapsed = ConnectionReadiness(method="oauth2", live_connection_active=False)
        assert connection_state(lapsed) == "expired"
        # No auth needed at all -> connected.
        assert connection_state(ConnectionReadiness(method="none")) == "connected"


# --------------------------------------------------- templates (pure)


class TestTemplates:
    def test_every_template_is_a_valid_plugin(self) -> None:
        for template in INTEGRATION_TEMPLATES:
            manifest = validate_manifest(template.manifest())  # raises on any problem
            assert manifest.key == template.key
            # Marketplace category and coarse plugin category are both known.
            assert is_known_category(template.category)
            assert template.plugin_category in PLUGIN_CATEGORIES
            # Certification is a known tier.
            certification_rank(template.certification)
            # Capabilities and events stay inside the platform's vocabularies.
            assert set(template.capabilities) <= set(PLUGIN_CAPABILITIES)
            assert set(template.events) <= set(WEBHOOK_EVENT_TYPES)

    def test_lookup(self) -> None:
        assert integration_template("slack").vendor == "Slack"
        with pytest.raises(KeyError):
            integration_template("nope")

    def test_named_providers_present(self) -> None:
        keys = {t.key for t in INTEGRATION_TEMPLATES}
        assert {"google_workspace", "microsoft_365", "slack", "stripe",
                "docusign", "twilio", "zapier", "openai", "anthropic",
                "whatsapp_cloud"} <= keys


# --------------------------------------------------- health rollup (pure)


class TestHealthRollup:
    def _signals(self, **over: Any) -> IntegrationHealthSignals:
        base: dict[str, Any] = {
            "installed": True,
            "enabled": True,
            "diagnostic_health": None,
            "connection_health": None,
            "auth_ready": True,
        }
        base.update(over)
        return IntegrationHealthSignals(**base)

    def test_not_installed(self) -> None:
        assert integration_health(self._signals(installed=False)) == "not_installed"

    def test_healthy(self) -> None:
        assert integration_health(self._signals()) == "healthy"

    def test_paused_is_degraded(self) -> None:
        assert integration_health(self._signals(enabled=False)) == "degraded"

    def test_missing_auth_is_down(self) -> None:
        assert integration_health(self._signals(auth_ready=False)) == "down"

    def test_worst_signal_wins(self) -> None:
        assert (
            integration_health(self._signals(diagnostic_health="unhealthy")) == "down"
        )
        assert (
            integration_health(self._signals(connection_health="degraded"))
            == "degraded"
        )

    def test_summary(self) -> None:
        summary = summarize_health(["healthy", "degraded", "down", "healthy"])
        assert summary.installed == 4 and summary.healthy == 2
        assert summary.degraded == 1 and summary.down == 1
        assert summary.attention == 2


# --------------------------------------------------- registry (db)


class TestRegistry:
    async def test_sync_is_global_idempotent_and_shared(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        user = await make_user(db, organization, "sync@vantage.example")
        registry = IntegrationRegistryService(db, _auth(organization, user.id))
        count = await registry.sync_templates(user)
        assert count == len(INTEGRATION_TEMPLATES)

        # Idempotent: a second sync refreshes, does not duplicate.
        await registry.sync_templates(user)
        listings = await registry.discover()
        keys = [listing.key for listing in listings]
        assert keys.count("slack") == 1
        assert len(listings) == len(INTEGRATION_TEMPLATES)

        # Curated listings are visible to a different tenant too.
        them = await make_user(db, other_organization, "them@meridian.example")
        their = await IntegrationRegistryService(
            db, _auth(other_organization, them.id)
        ).discover()
        assert {"stripe", "openai"} <= {listing.key for listing in their}

    async def test_discovery_filters_and_search(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "disc@vantage.example")
        registry = IntegrationRegistryService(db, _auth(organization, user.id))
        await registry.sync_templates(user)

        ai = await registry.discover(category="ai")
        assert {listing.key for listing in ai} == {"openai", "anthropic"}

        oauth = await registry.discover(auth_method="oauth2")
        assert {"google_workspace", "microsoft_365", "docusign"} <= {
            listing.key for listing in oauth
        }

        found = await registry.discover(query="stripe")
        assert [listing.key for listing in found] == ["stripe"]
        assert found[0].certified is True

    async def test_dashboard_counts(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "dash@vantage.example")
        auth = _auth(organization, user.id)
        registry = IntegrationRegistryService(db, auth)
        await registry.sync_templates(user)

        overview = await registry.dashboard()
        assert overview.total_listings == len(INTEGRATION_TEMPLATES)
        assert overview.certified >= 1
        assert overview.by_category.get("ai") == 2
        assert overview.installed == 0

    async def test_sync_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "nom@vantage.example")
        registry = IntegrationRegistryService(
            db, _auth(organization, user.id, manage=False)
        )
        with pytest.raises(PermissionDeniedError):
            await registry.sync_templates(user)


# --------------------------------------------------- installation (db)


class TestInstallation:
    async def _sync_and_find(
        self, db: AsyncSession, auth: AuthorizationContext, user, key: str
    ):  # type: ignore[no-untyped-def]
        registry = IntegrationRegistryService(db, auth)
        await registry.sync_templates(user)
        listing = next(
            listing for listing in await registry.discover() if listing.key == key
        )
        return listing

    async def test_install_provisions_plugin_and_seals_secrets(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "inst@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        listing = await self._sync_and_find(db, auth, user, "slack")

        installs = IntegrationInstallationService(db, auth, settings)
        installed = await installs.install(
            user,
            listing.id,
            InstallListingRequest(
                config={"channel": "#deals", "webhook_url": "https://hooks/x"}
            ),
        )
        assert installed.listing_key == "slack"
        assert installed.status == "installed"
        # The secret is sealed and never read back.
        assert installed.secret_keys == ["webhook_url"]

        row = (
            await db.execute(
                select(PluginInstallation).where(
                    PluginInstallation.id == installed.installation_id
                )
            )
        ).scalar_one()
        assert row.config == {"channel": "#deals"}
        assert is_token(row.secrets["webhook_url"])

        # The listing now reports installed for this tenant.
        registry = IntegrationRegistryService(db, auth)
        refreshed = next(
            listing_ for listing_ in await registry.discover()
            if listing_.key == "slack"
        )
        assert refreshed.installed is True

        # The marketplace install is recorded.
        audit = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "marketplace.integration.installed"
                )
            )
        ).scalars().all()
        assert len(audit) == 1

        listed = await installs.list_installed()
        assert [i.listing_key for i in listed] == ["slack"]

    async def test_lifecycle_and_uninstall(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "life@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        listing = await self._sync_and_find(db, auth, user, "stripe")

        installs = IntegrationInstallationService(db, auth, settings)
        await installs.install(
            user, listing.id, InstallListingRequest(config={"api_key": "sk_test"})
        )

        enabled = await installs.enable(user, listing.id)
        assert enabled.status == "enabled"
        disabled = await installs.disable(user, listing.id)
        assert disabled.status == "disabled"

        await installs.uninstall(user, listing.id)
        assert await installs.list_installed() == []

    async def test_install_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "noinst@vantage.example")
        auth = _auth(organization, user.id)
        # Sync as an admin first so a listing exists.
        listing = await self._sync_and_find(db, auth, user, "openai")
        installs = IntegrationInstallationService(
            db, _auth(organization, user.id, manage=False), _settings()
        )
        with pytest.raises(PermissionDeniedError):
            await installs.install(user, listing.id, InstallListingRequest())


# --------------------------------------------------- health (db)


class TestHealth:
    async def test_health_folds_diagnostics(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "health@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        registry = IntegrationRegistryService(db, auth)
        await registry.sync_templates(user)
        slack = next(
            listing for listing in await registry.discover() if listing.key == "slack"
        )

        health = IntegrationHealthService(db, auth, settings)
        # Not installed yet.
        before_install = await health.health(slack.id)
        assert before_install.installed is False
        assert before_install.health == "not_installed"

        installs = IntegrationInstallationService(db, auth, settings)
        # Install with no config: the required secret is unset, and the plugin's
        # required-config diagnostic fails -> the rolled-up health is down.
        installed = await installs.install(user, slack.id, InstallListingRequest())
        unconfigured = await health.health(slack.id)
        assert unconfigured.installed is True
        assert unconfigured.health == "down"
        assert unconfigured.auth_ready is False

        # Configure the required secret and channel, then enable. The auth is now
        # in place and the diagnostics pass, so it is no longer down.
        await self._configure_and_enable(
            db, auth, settings, user, installed.installation_id
        )

        after = await health.health(slack.id)
        assert after.auth_ready is True
        assert after.health in ("healthy", "degraded")
        assert all(
            c.status != "fail" for c in after.checks if c.name in ("config", "lifecycle")
        )

    @staticmethod
    async def _configure_and_enable(
        db: AsyncSession, auth: AuthorizationContext, settings: Settings, user, installation_id
    ) -> None:  # type: ignore[no-untyped-def]
        # Configuration and lifecycle are the plugin platform's job — the
        # marketplace reuses them rather than restating them.
        from app.schemas.plugin import ConfigureRequest
        from app.services.plugin import PluginInstallationService

        service = PluginInstallationService(db, auth, settings)
        await service.configure(
            user,
            installation_id,
            ConfigureRequest(
                config={"channel": "#deals", "webhook_url": "https://hooks/x"}
            ).config,
        )
        await service.enable(user, installation_id)

    async def test_health_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "nohealth@vantage.example")
        auth = _auth(organization, user.id)
        registry = IntegrationRegistryService(db, auth)
        await registry.sync_templates(user)
        listing = (await registry.discover())[0]
        health = IntegrationHealthService(
            db, _auth(organization, user.id, manage=False), _settings()
        )
        with pytest.raises(PermissionDeniedError):
            await health.health(listing.id)
