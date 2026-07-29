"""Marketplace operations (Phase 9.3).

The properties that carry the milestone:

  * **The publication lifecycle is an explicit machine** — draft -> review ->
    approved -> published, with illegal transitions refused precisely.
  * **Publishing provisions a plugin and cuts a version**, and a listing is only
    installable once published.
  * **Installing records operational history** — a tracking row per install, kept
    as history after uninstall — and delegates the runtime to the plugin platform.
  * **Versions are ordered and compatibility-checked**, and the upgrade foundation
    reports readiness and records a start.
  * **Analytics roll up state, adoption and health**; tenant isolation holds; and
    every operation is gated on `settings.manage` and audited.
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
from app.marketplace.analytics import (
    adoption_metrics,
    adoption_signal,
    operational_health,
    summarize_states,
)
from app.marketplace.installation import (
    InstallationStateError,
    is_active,
    is_history,
)
from app.marketplace.installation import next_state as next_install_state
from app.marketplace.lifecycle import (
    LISTING_STATES,
    PublicationError,
    is_installable,
    is_public,
)
from app.marketplace.lifecycle import next_state as next_listing_state
from app.marketplace.versioning import (
    assess_upgrade,
    check_compatibility,
    is_newer,
    latest,
)
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.schemas.marketplace import InstallListingRequest, ListingDraftCreate
from app.sdk.version import SDK_VERSION
from app.services.marketplace import IntegrationRegistryService
from app.services.marketplace_operations import MarketplaceOperationsService
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


def _draft(key: str = "acme_ops", **over: Any) -> ListingDraftCreate:
    manifest: dict[str, Any] = {
        "key": key,
        "name": "Acme Ops",
        "version": "1.0.0",
        "description": "An Acme operational integration.",
        "publisher": "Acme Inc",
        "category": "integration",
        "capabilities": ["events.subscribe"],
        "events": ["deal.created"],
        "config_schema": [
            {"key": "token", "label": "Token", "type": "secret", "required": True},
        ],
    }
    base: dict[str, Any] = {
        "manifest": manifest,
        "vendor": "Acme Inc",
        "category": "crm",
        "summary": "Acme CRM sync.",
        "auth_method": "api_key",
    }
    base.update(over)
    return ListingDraftCreate(**base)


# =============================================================== pure domain


class TestLifecycle:
    def test_full_path(self) -> None:
        state = "draft"
        for action, expected in (
            ("submit", "review"),
            ("approve", "approved"),
            ("publish", "published"),
            ("deprecate", "deprecated"),
            ("retire", "retired"),
        ):
            state = next_listing_state(state, action)
            assert state == expected

    def test_illegal_transitions(self) -> None:
        with pytest.raises(PublicationError):
            next_listing_state("draft", "publish")  # never approved
        with pytest.raises(PublicationError):
            next_listing_state("published", "approve")
        with pytest.raises(PublicationError):
            next_listing_state("draft", "teleport")

    def test_reject_returns_to_draft(self) -> None:
        assert next_listing_state("review", "reject") == "draft"

    def test_predicates(self) -> None:
        assert is_installable("published") and not is_installable("draft")
        assert is_public("published") and is_public("deprecated")
        assert not is_public("draft")
        assert set(LISTING_STATES) == {
            "draft", "review", "approved", "published", "deprecated", "retired"
        }


class TestVersioning:
    def test_ordering(self) -> None:
        assert is_newer("1.1.0", "1.0.0") is True
        assert is_newer("1.0.0", "1.0.0") is False
        assert latest(["1.0.0", "2.1.0", "1.5.0"]) == "2.1.0"
        assert latest([]) is None

    def test_compatibility(self) -> None:
        ok = check_compatibility("1.0.0", {"sdk_version": SDK_VERSION})
        assert ok.compatible is True
        bad = check_compatibility("1.0.0", {"sdk_version": "2.0.0"})
        assert bad.compatible is False
        # No SDK requirement -> compatible.
        assert check_compatibility("1.0.0", {}).compatible is True

    def test_assess_upgrade(self) -> None:
        compat = check_compatibility("1.1.0", {})
        available = assess_upgrade("1.0.0", "1.1.0", compat)
        assert available.upgrade_available is True and available.compatible is True
        current = assess_upgrade("1.1.0", "1.1.0", compat)
        assert current.upgrade_available is False


class TestInstallationStates:
    def test_transitions(self) -> None:
        assert next_install_state("pending", "activate") == "active"
        assert next_install_state("active", "begin_upgrade") == "upgrading"
        assert next_install_state("upgrading", "complete_upgrade") == "active"
        assert next_install_state("active", "uninstall") == "uninstalled"

    def test_illegal(self) -> None:
        with pytest.raises(InstallationStateError):
            next_install_state("uninstalled", "activate")

    def test_predicates(self) -> None:
        assert is_active("active") and is_active("upgrading")
        assert is_history("uninstalled") and not is_history("active")


class TestAnalytics:
    def test_adoption_signal(self) -> None:
        assert adoption_signal(0) == "none"
        assert adoption_signal(2) == "emerging"
        assert adoption_signal(5) == "growing"
        assert adoption_signal(20) == "popular"

    def test_summarize_states(self) -> None:
        counts = summarize_states(["draft", "draft", "published"], LISTING_STATES)
        assert counts["draft"] == 2 and counts["published"] == 1
        assert counts["retired"] == 0  # zero-filled

    def test_adoption_metrics_ranked(self) -> None:
        metrics = adoption_metrics({"a": 1, "b": 12, "c": 4})
        assert [m.listing_key for m in metrics] == ["b", "c", "a"]
        assert metrics[0].signal == "popular"

    def test_operational_health(self) -> None:
        assert operational_health(0, 0) == "critical"
        assert operational_health(5, 0) == "attention"
        assert operational_health(5, 3) == "healthy"


# =============================================================== publication (db)


class TestPublicationWorkflow:
    async def test_full_publication_path(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "pub@vantage.example")
        auth = _auth(organization, user.id)
        ops = MarketplaceOperationsService(db, auth, _settings())

        draft = await ops.create_draft(user, _draft())
        assert draft.status == "draft" and draft.is_first_party is False

        assert (await ops.submit(user, draft.id)).status == "review"
        approved = await ops.review(user, draft.id, "approved", "looks good")
        assert approved.status == "approved"
        published = await ops.publish(user, draft.id)
        assert published.status == "published"

        # Publishing provisioned the plugin and cut a version.
        versions = await ops.list_versions(draft.id)
        assert [v.version for v in versions] == ["1.0.0"]

        # The audit trail records the pipeline.
        actions = {
            row.action
            for row in (
                await db.execute(select(AuditLog).where(AuditLog.entity_id == draft.id))
            ).scalars().all()
        }
        assert {
            "marketplace.listing.created",
            "marketplace.listing.submitted",
            "marketplace.listing.approved",
            "marketplace.listing.published",
        } <= actions

    async def test_illegal_transition_refused(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "bad@vantage.example")
        ops = MarketplaceOperationsService(db, _auth(organization, user.id), _settings())
        draft = await ops.create_draft(user, _draft(key="acme_bad"))
        # Cannot publish something never approved.
        with pytest.raises(AppError):
            await ops.publish(user, draft.id)

    async def test_reject_records_review(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "rej@vantage.example")
        ops = MarketplaceOperationsService(db, _auth(organization, user.id), _settings())
        draft = await ops.create_draft(user, _draft(key="acme_rej"))
        await ops.submit(user, draft.id)
        rejected = await ops.review(user, draft.id, "rejected", "needs work")
        assert rejected.status == "draft"
        rejected_audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "marketplace.listing.rejected")
            )
        ).scalars().all()
        assert len(rejected_audit) == 1

    async def test_tenant_isolation(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        user = await make_user(db, organization, "own@vantage.example")
        ops = MarketplaceOperationsService(db, _auth(organization, user.id), _settings())
        draft = await ops.create_draft(user, _draft(key="acme_private_ops"))

        # A different tenant neither sees nor can act on the private draft.
        them = await make_user(db, other_organization, "them@meridian.example")
        their_registry = IntegrationRegistryService(
            db, _auth(other_organization, them.id)
        )
        assert "acme_private_ops" not in {
            listing.key for listing in await their_registry.discover()
        }
        their_ops = MarketplaceOperationsService(
            db, _auth(other_organization, them.id), _settings()
        )
        with pytest.raises(NotFoundError):
            await their_ops.submit(them, draft.id)

    async def test_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "nom@vantage.example")
        ops = MarketplaceOperationsService(
            db, _auth(organization, user.id, manage=False), _settings()
        )
        with pytest.raises(PermissionDeniedError):
            await ops.create_draft(user, _draft(key="acme_nom"))


# =============================================================== install (db)


class TestOperationalInstall:
    async def _sync(self, db: AsyncSession, auth: AuthorizationContext, user):  # type: ignore[no-untyped-def]
        registry = IntegrationRegistryService(db, auth)
        await registry.sync_templates(user)
        return {listing.key: listing for listing in await registry.discover()}

    async def test_install_tracks_history_and_uninstall(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "inst@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        catalog = await self._sync(db, auth, user)
        stripe = catalog["stripe"]

        ops = MarketplaceOperationsService(db, auth, settings)
        installed = await ops.install(
            user, stripe.id, InstallListingRequest(config={"api_key": "sk_test"})
        )
        assert installed.listing_key == "stripe"

        active = await ops.list_installed()
        assert [row.listing_key for row in active] == ["stripe"]
        assert active[0].installed_version == "1.0.0"
        assert active[0].status == "active"

        # integration_installed is audited (via the plugin platform install).
        installed_audit = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "marketplace.integration.installed"
                )
            )
        ).scalars().all()
        assert len(installed_audit) == 1

        await ops.uninstall(user, stripe.id)
        assert await ops.list_installed() == []
        # History survives the uninstall.
        history = await ops.installation_history()
        assert len(history) == 1 and history[0].status == "uninstalled"
        assert history[0].uninstalled_at is not None

        uninstalled_audit = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "marketplace.integration.uninstalled"
                )
            )
        ).scalars().all()
        assert len(uninstalled_audit) == 1

    async def test_cannot_install_unpublished(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "unpub@vantage.example")
        auth = _auth(organization, user.id)
        ops = MarketplaceOperationsService(db, auth, _settings())
        draft = await ops.create_draft(user, _draft(key="acme_unpub"))
        with pytest.raises(AppError):
            await ops.install(user, draft.id, InstallListingRequest())


# =============================================================== versions & upgrade (db)


class TestVersionsAndUpgrade:
    async def test_create_version_and_upgrade(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "ver@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        ops = MarketplaceOperationsService(db, auth, settings)

        # Author, publish, install at 1.0.0.
        draft = await ops.create_draft(user, _draft(key="acme_ver"))
        await ops.submit(user, draft.id)
        await ops.review(user, draft.id, "approved", None)
        await ops.publish(user, draft.id)
        await ops.install(
            user, draft.id, InstallListingRequest(config={"token": "x"})
        )

        # No upgrade available yet.
        readiness = await ops.upgrade_readiness(draft.id)
        assert readiness.upgrade_available is False

        # A non-newer version is refused; a newer one is accepted.
        with pytest.raises(AppError):
            await ops.create_version(user, draft.id, "1.0.0", {})
        new_version = await ops.create_version(
            user, draft.id, "1.1.0", {"sdk_version": SDK_VERSION}
        )
        assert new_version.version == "1.1.0"

        # Now an upgrade is available and compatible; starting it is audited.
        readiness = await ops.prepare_upgrade(user, draft.id)
        assert readiness.upgrade_available is True and readiness.compatible is True
        assert readiness.latest_version == "1.1.0"
        upgrade_audit = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "marketplace.integration.upgrade_started"
                )
            )
        ).scalars().all()
        assert len(upgrade_audit) == 1

    async def test_compatibility_endpoint(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "compat@vantage.example")
        auth = _auth(organization, user.id)
        catalog_registry = IntegrationRegistryService(db, auth)
        await catalog_registry.sync_templates(user)
        listing = next(
            listing for listing in await catalog_registry.discover()
            if listing.key == "openai"
        )
        ops = MarketplaceOperationsService(db, auth, _settings())
        result = await ops.check_compatibility(listing.id)
        assert result.compatible is True and result.version == "1.0.0"


# =============================================================== analytics (db)


class TestAnalyticsService:
    async def test_analytics_rollup(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "an@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        registry = IntegrationRegistryService(db, auth)
        await registry.sync_templates(user)
        stripe = next(
            listing for listing in await registry.discover()
            if listing.key == "stripe"
        )
        ops = MarketplaceOperationsService(db, auth, settings)
        await ops.install(
            user, stripe.id, InstallListingRequest(config={"api_key": "x"})
        )

        analytics = await ops.analytics()
        assert analytics.published >= 1
        assert analytics.active_installs == 1
        assert analytics.total_installs == 1
        assert analytics.operational_health == "healthy"
        assert any(a.listing_key == "stripe" for a in analytics.adoption)

    async def test_analytics_requires_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "an2@vantage.example")
        ops = MarketplaceOperationsService(
            db, _auth(organization, user.id, manage=False), _settings()
        )
        with pytest.raises(PermissionDeniedError):
            await ops.analytics()
