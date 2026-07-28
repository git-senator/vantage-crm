"""Integration marketplace services.

Three services, and every one of them is thin because the heavy lifting already
exists:

  * ``IntegrationRegistryService`` — discovery over the curated listing registry,
    the category and certification vocabularies, and the one-time sync that turns
    the code-level templates into listings (provisioning each one's plugin through
    the plugin platform, never a second catalog).
  * ``IntegrationInstallationService`` — the installation workflow. Installing a
    listing *is* installing its plugin: it delegates to the plugin platform's
    installation service, which seals secrets, wires subscriptions and gates on
    the required feature. Enable, disable and uninstall are the same delegation.
  * ``IntegrationHealthService`` — health and diagnostics, folding the Phase 9.1
    plugin diagnostics and (when a listing wraps a live provider) the Phase 7.7
    connection's health into one rating through the deterministic rollup.

Nothing here re-implements OAuth, tokens, sync, subscriptions or the plugin
lifecycle. The marketplace is the curation and provisioning layer; the runtime is
the plugin platform and the integration runtime it already ships. Management is
gated on ``settings.manage``; browsing the catalog needs only an authenticated
member.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.marketplace.categories import INTEGRATION_CATEGORIES
from app.marketplace.certification import (
    CERTIFICATION_TIERS,
    certification_rank,
    is_certified,
)
from app.marketplace.connections import ConnectionReadiness, connection_state
from app.marketplace.health import (
    IntegrationHealthSignals,
    MarketplaceHealthSummary,
    integration_health,
    summarize_health,
)
from app.marketplace.templates import INTEGRATION_TEMPLATES
from app.models.marketplace import IntegrationListing
from app.models.plugin import Plugin, PluginInstallation
from app.models.user import User
from app.repositories.integration import IntegrationConnectionRepository
from app.repositories.marketplace import IntegrationListingRepository
from app.repositories.plugin import PluginInstallationRepository, PluginRepository
from app.schemas.marketplace import (
    CertificationTierRead,
    InstalledIntegrationRead,
    InstallListingRequest,
    IntegrationCategoryRead,
    IntegrationDiagnosticCheckRead,
    IntegrationHealthRead,
    IntegrationListingRead,
    MarketplaceOverview,
)
from app.schemas.plugin import InstallRequest
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext

MANAGE_PERMISSION = "settings.manage"


def _required_secret_keys(manifest: dict[str, Any]) -> set[str]:
    """The secret config keys a listing's manifest requires be set."""
    schema = manifest.get("config_schema") or []
    return {
        f["key"]
        for f in schema
        if isinstance(f, dict) and f.get("type") == "secret" and f.get("required")
    }


def _listing_read(row: IntegrationListing, *, installed: bool) -> IntegrationListingRead:
    return IntegrationListingRead(
        id=row.id,
        key=row.key,
        name=row.name,
        vendor=row.vendor,
        category=row.category,
        summary=row.summary,
        description=row.description,
        auth_method=row.auth_method,
        oauth_scopes=list(row.oauth_scopes),
        provider_key=row.provider_key,
        certification=row.certification,
        certified=is_certified(row.certification),
        capabilities=list(row.capabilities),
        event_types=list(row.event_types),
        required_feature=row.required_feature,
        docs_url=row.docs_url,
        is_first_party=row.is_first_party,
        status=row.status,
        installed=installed,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# =========================================================== registry / discovery


class IntegrationRegistryService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = IntegrationListingRepository(session)
        self.plugins = PluginRepository(session)
        self.installs = PluginInstallationRepository(session)
        self.audit = AuditService(session)

    def categories(self) -> list[IntegrationCategoryRead]:
        return [
            IntegrationCategoryRead(key=c.key, title=c.title, description=c.description)
            for c in INTEGRATION_CATEGORIES.values()
        ]

    def certification_tiers(self) -> list[CertificationTierRead]:
        return [
            CertificationTierRead(
                key=tier, rank=certification_rank(tier), certified=is_certified(tier)
            )
            for tier in CERTIFICATION_TIERS
        ]

    async def discover(
        self,
        *,
        category: str | None = None,
        certification: str | None = None,
        auth_method: str | None = None,
        query: str | None = None,
    ) -> list[IntegrationListingRead]:
        """Marketplace browse — curated listings plus this tenant's own.
        Available to any authenticated member; no management gate."""
        rows = await self.repo.discover(
            self.auth.organization_id,
            category=category,
            certification=certification,
            auth_method=auth_method,
            query=query,
        )
        installed = await self._installed_keys()
        return [_listing_read(row, installed=row.key in installed) for row in rows]

    async def get(self, listing_id: UUID) -> IntegrationListingRead:
        listing = await self._load(listing_id)
        installed = await self._installed_keys()
        return _listing_read(listing, installed=listing.key in installed)

    async def sync_templates(self, actor: User) -> int:
        """Upsert the curated templates as listings, provisioning each one's
        plugin. Idempotent: an existing curated listing is refreshed, not
        duplicated, and the plugin behind it is never clobbered."""
        self.auth.require(MANAGE_PERMISSION)
        from app.services.plugin import PluginRegistryService

        registry = PluginRegistryService(self.session, self.auth)
        synced = 0
        for template in INTEGRATION_TEMPLATES:
            certification_rank(template.certification)  # validate the tier
            manifest = template.manifest()
            # Provision the plugin behind the listing (idempotent, non-clobbering).
            await registry.ensure_global_plugin(actor, manifest)

            existing = await self.repo.get_by_key(
                self.auth.organization_id, template.key
            )
            if existing is not None:
                if existing.publisher_organization_id is not None:
                    # A tenant owns this key privately; do not clobber it.
                    continue
                self._apply_template(existing, template, manifest)
            else:
                listing = IntegrationListing(
                    publisher_organization_id=None,
                    created_by=actor.id,
                    is_first_party=True,
                )
                self._apply_template(listing, template, manifest)
                self.session.add(listing)
            synced += 1
        await self.session.flush()
        await self.audit.record(
            action=AuditAction.MARKETPLACE_TEMPLATES_SYNCED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="integration_marketplace",
            entity_id=None,
            metadata={"synced": synced},
        )
        return synced

    async def dashboard(self) -> MarketplaceOverview:
        self.auth.require(MANAGE_PERMISSION)
        org = self.auth.organization_id
        total = await self.repo.count_visible(org)
        certified = await self.repo.count_certified(org)
        by_category = await self.repo.count_by_category(org)

        installed = await _installed_integrations(
            self.repo, self.plugins, self.installs, org
        )
        ratings = [_lightweight_health(listing, inst) for listing, _, inst in installed]
        summary: MarketplaceHealthSummary = summarize_health(ratings)
        return MarketplaceOverview(
            total_listings=total,
            certified=certified,
            by_category=by_category,
            installed=summary.installed,
            healthy=summary.healthy,
            degraded=summary.degraded,
            down=summary.down,
        )

    async def _installed_keys(self) -> set[str]:
        rows = await self.installs.list_for_org(self.auth.organization_id)
        if not rows:
            return set()
        by_id = {p.id: p.key for p in await self.plugins.list_catalog(
            self.auth.organization_id
        )}
        return {by_id[r.plugin_id] for r in rows if r.plugin_id in by_id}

    def _apply_template(
        self, row: IntegrationListing, template: Any, manifest: dict[str, Any]
    ) -> None:
        row.key = template.key
        row.name = template.name
        row.vendor = template.vendor
        row.category = template.category
        row.summary = template.summary
        row.description = template.description
        row.auth_method = template.auth.method
        row.oauth_scopes = list(template.oauth_scopes)
        row.provider_key = template.auth.provider_key
        row.certification = template.certification
        row.manifest = manifest
        row.capabilities = list(template.capabilities)
        row.event_types = list(template.events)
        row.required_feature = template.required_feature
        row.docs_url = template.docs_url
        row.status = "listed"

    async def _load(self, listing_id: UUID) -> IntegrationListing:
        listing = await self.repo.get_visible(listing_id, self.auth.organization_id)
        if listing is None:
            raise NotFoundError("Integration listing not found.")
        return listing


# =========================================================== installation workflow


class IntegrationInstallationService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.listings = IntegrationListingRepository(session)
        self.plugins = PluginRepository(session)
        self.installs = PluginInstallationRepository(session)
        self.audit = AuditService(session)

    async def install(
        self, actor: User, listing_id: UUID, payload: InstallListingRequest
    ) -> InstalledIntegrationRead:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load(listing_id)
        if listing.status == "deprecated":
            raise AppError("This integration is deprecated and cannot be installed.")

        from app.services.plugin import PluginInstallationService, PluginRegistryService

        # Provision the plugin behind the listing if it is not in the catalog yet,
        # then install it through the plugin platform (which seals secrets, wires
        # subscriptions and enforces the required feature).
        plugin = await PluginRegistryService(
            self.session, self.auth
        ).ensure_global_plugin(actor, dict(listing.manifest))
        installation = await PluginInstallationService(
            self.session, self.auth, self.settings
        ).install(
            actor,
            InstallRequest(
                plugin_id=plugin.id,
                granted_capabilities=payload.granted_capabilities,
                config=payload.config,
            ),
        )

        await self.audit.record(
            action=AuditAction.MARKETPLACE_INTEGRATION_INSTALLED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="integration_listing",
            entity_id=listing.id,
            metadata={"listing_key": listing.key, "plugin_id": str(plugin.id)},
        )

        row = await self.installs.get(installation.id, self.auth.organization_id)
        assert row is not None  # just created
        return _installed_read(listing, plugin, row)

    async def list_installed(self) -> list[InstalledIntegrationRead]:
        self.auth.require(MANAGE_PERMISSION)
        installed = await _installed_integrations(
            self.listings, self.plugins, self.installs, self.auth.organization_id
        )
        return [
            _installed_read(listing, plugin, inst)
            for listing, plugin, inst in installed
        ]

    async def enable(self, actor: User, listing_id: UUID) -> InstalledIntegrationRead:
        return await self._transition(actor, listing_id, "enable")

    async def disable(self, actor: User, listing_id: UUID) -> InstalledIntegrationRead:
        return await self._transition(actor, listing_id, "disable")

    async def uninstall(self, actor: User, listing_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        _, _, installation = await self._resolve(listing_id)
        from app.services.plugin import PluginInstallationService

        await PluginInstallationService(
            self.session, self.auth, self.settings
        ).uninstall(actor, installation.id)

    async def _transition(
        self, actor: User, listing_id: UUID, action: str
    ) -> InstalledIntegrationRead:
        self.auth.require(MANAGE_PERMISSION)
        listing, plugin, installation = await self._resolve(listing_id)
        from app.services.plugin import PluginInstallationService

        service = PluginInstallationService(self.session, self.auth, self.settings)
        if action == "enable":
            await service.enable(actor, installation.id)
        else:
            await service.disable(actor, installation.id)
        row = await self.installs.get(installation.id, self.auth.organization_id)
        assert row is not None
        return _installed_read(listing, plugin, row)

    async def _resolve(
        self, listing_id: UUID
    ) -> tuple[IntegrationListing, Plugin, PluginInstallation]:
        listing = await self._load(listing_id)
        plugin = await self.plugins.get_by_key(
            self.auth.organization_id, listing.key
        )
        if plugin is None:
            raise NotFoundError("This integration is not installed.")
        installation = await self.installs.get_by_plugin(
            self.auth.organization_id, plugin.id
        )
        if installation is None:
            raise NotFoundError("This integration is not installed.")
        return listing, plugin, installation

    async def _load(self, listing_id: UUID) -> IntegrationListing:
        listing = await self.listings.get_visible(
            listing_id, self.auth.organization_id
        )
        if listing is None:
            raise NotFoundError("Integration listing not found.")
        return listing


# =========================================================== health & diagnostics


class IntegrationHealthService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.listings = IntegrationListingRepository(session)
        self.plugins = PluginRepository(session)
        self.installs = PluginInstallationRepository(session)
        self.connections = IntegrationConnectionRepository(session)

    async def health(self, listing_id: UUID) -> IntegrationHealthRead:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load(listing_id)
        plugin = await self.plugins.get_by_key(
            self.auth.organization_id, listing.key
        )
        installation = (
            await self.installs.get_by_plugin(self.auth.organization_id, plugin.id)
            if plugin is not None
            else None
        )

        if installation is None:
            readiness = ConnectionReadiness(method=listing.auth_method)
            signals = IntegrationHealthSignals(installed=False, enabled=False)
            return IntegrationHealthRead(
                listing_key=listing.key,
                installed=False,
                status=None,
                health=integration_health(signals),
                connection_state=connection_state(readiness),
                auth_ready=readiness.is_ready,
                diagnostic_health=None,
                connection_health=None,
                checks=[],
            )

        # Reuse the Phase 9.1 plugin diagnostics wholesale.
        from app.services.sdk import PluginDiagnosticsService

        report = await PluginDiagnosticsService(
            self.session, self.auth, self.settings
        ).diagnose(installation.id)

        connection_health, live_active = await self._connection_signal(listing)
        readiness = ConnectionReadiness(
            method=listing.auth_method,
            required_secret_keys=frozenset(_required_secret_keys(listing.manifest)),
            present_secret_keys=frozenset(installation.secrets.keys()),
            live_connection_active=live_active,
        )
        signals = IntegrationHealthSignals(
            installed=True,
            enabled=installation.status == "enabled",
            diagnostic_health=report.health,
            connection_health=connection_health,
            auth_ready=readiness.is_ready,
        )
        return IntegrationHealthRead(
            listing_key=listing.key,
            installed=True,
            status=installation.status,
            health=integration_health(signals),
            connection_state=connection_state(readiness),
            auth_ready=readiness.is_ready,
            diagnostic_health=report.health,
            connection_health=connection_health,
            checks=[
                IntegrationDiagnosticCheckRead(
                    name=c.name, status=c.status, detail=c.detail
                )
                for c in report.checks
            ],
        )

    async def _connection_signal(
        self, listing: IntegrationListing
    ) -> tuple[str | None, bool | None]:
        """A live Phase 7.7 connection's health for the listing's provider, if
        the listing wraps one and a connection exists. Read-only reuse — the
        marketplace never mutates a connection."""
        if not listing.provider_key:
            return None, None
        rows = await self.connections.list_for_org(self.auth.organization_id)
        match = next(
            (c for c in rows if c.provider == listing.provider_key), None
        )
        if match is None:
            return None, None
        return match.health, match.is_active

    async def _load(self, listing_id: UUID) -> IntegrationListing:
        listing = await self.listings.get_visible(
            listing_id, self.auth.organization_id
        )
        if listing is None:
            raise NotFoundError("Integration listing not found.")
        return listing


# =========================================================== shared helpers


async def _installed_integrations(
    listings_repo: IntegrationListingRepository,
    plugins_repo: PluginRepository,
    installs_repo: PluginInstallationRepository,
    organization_id: UUID,
) -> list[tuple[IntegrationListing, Plugin, PluginInstallation]]:
    """The tenant's installed plugins that are marketplace integrations — i.e.
    an installation whose plugin key matches a visible listing."""
    installations = await installs_repo.list_for_org(organization_id)
    if not installations:
        return []
    listings_by_key = {
        row.key: row for row in await listings_repo.discover(organization_id)
    }
    plugins_by_id = {
        p.id: p for p in await plugins_repo.list_catalog(organization_id)
    }
    result: list[tuple[IntegrationListing, Plugin, PluginInstallation]] = []
    for inst in installations:
        plugin = plugins_by_id.get(inst.plugin_id)
        if plugin is None:
            continue
        listing = listings_by_key.get(plugin.key)
        if listing is None:
            continue
        result.append((listing, plugin, inst))
    return result


def _lightweight_health(
    listing: IntegrationListing, installation: PluginInstallation
) -> str:
    """A dashboard-grade health that avoids per-installation diagnostics — it
    folds the lifecycle state and auth readiness only."""
    readiness = ConnectionReadiness(
        method=listing.auth_method,
        required_secret_keys=frozenset(_required_secret_keys(listing.manifest)),
        present_secret_keys=frozenset(installation.secrets.keys()),
    )
    return integration_health(
        IntegrationHealthSignals(
            installed=True,
            enabled=installation.status == "enabled",
            auth_ready=readiness.is_ready,
        )
    )


def _installed_read(
    listing: IntegrationListing, plugin: Plugin, installation: PluginInstallation
) -> InstalledIntegrationRead:
    return InstalledIntegrationRead(
        listing_key=listing.key,
        name=listing.name,
        vendor=listing.vendor,
        category=listing.category,
        certification=listing.certification,
        installation_id=installation.id,
        plugin_id=plugin.id,
        status=installation.status,
        health=_lightweight_health(listing, installation),
        secret_keys=sorted(installation.secrets.keys()),
    )


__all__ = [
    "MANAGE_PERMISSION",
    "IntegrationHealthService",
    "IntegrationInstallationService",
    "IntegrationRegistryService",
]
