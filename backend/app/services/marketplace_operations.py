"""Marketplace operations — the lifecycle, publication and analytics service.

Phase 9.3 turns the marketplace from a registry into an operational platform. This
service owns the parts the 9.2 registry did not:

  * **Publication** — the draft -> review -> approved -> published path for a
    tenant-authored listing, its review decisions, and the version cut on publish.
  * **Versioning** — creating new versions of a listing and judging their
    compatibility against the SDK the platform runs.
  * **Operational installs** — installing and uninstalling an integration while
    recording an install-history row, and the foundation of the upgrade workflow.
  * **Analytics** — the operational counts, adoption signals and marketplace
    health an administrator watches.

Runtime is never re-implemented. Installing delegates to the 9.2 installation
service, which delegates to the plugin platform (sealing secrets, wiring
subscriptions, gating on the feature flag); publishing provisions a plugin through
the plugin registry. This service adds governance, tracking and analytics *around*
that runtime. Every operation is gated on ``settings.manage`` and runs under RLS.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.marketplace.analytics import (
    adoption_metrics,
    operational_health,
    summarize_states,
)
from app.marketplace.categories import is_known_category
from app.marketplace.connections import validate_auth_method
from app.marketplace.installation import next_state as next_install_state
from app.marketplace.lifecycle import (
    LISTING_STATES,
    PublicationError,
    is_installable,
)
from app.marketplace.lifecycle import next_state as next_listing_state
from app.marketplace.versioning import (
    UpgradeReadiness,
    assess_upgrade,
    check_compatibility,
    is_newer,
    latest,
)
from app.models.marketplace import (
    IntegrationInstallation,
    IntegrationListing,
    IntegrationReview,
    IntegrationVersion,
)
from app.models.user import User
from app.plugins.manifest import PluginManifest, validate_manifest
from app.repositories.marketplace import (
    IntegrationInstallationRepository,
    IntegrationListingRepository,
    IntegrationReviewRepository,
    IntegrationVersionRepository,
)
from app.repositories.plugin import PluginRepository
from app.schemas.marketplace import (
    AdoptionRead,
    CompatibilityResultRead,
    InstalledIntegrationRead,
    InstallListingRequest,
    IntegrationListingRead,
    IntegrationVersionRead,
    ListingDraftCreate,
    MarketplaceAnalyticsRead,
    OperationalInstallationRead,
    UpgradeReadinessRead,
)
from app.sdk.version import SDK_VERSION
from app.services.audit import AuditService
from app.services.marketplace import IntegrationInstallationService, _listing_read
from app.services.rbac import AuthorizationContext

MANAGE_PERMISSION = "settings.manage"


def _now() -> datetime:
    return datetime.now(UTC)


class MarketplaceOperationsService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.listings = IntegrationListingRepository(session)
        self.versions = IntegrationVersionRepository(session)
        self.installs = IntegrationInstallationRepository(session)
        self.reviews = IntegrationReviewRepository(session)
        self.plugins = PluginRepository(session)
        self.audit = AuditService(session)

    # ------------------------------------------------ publication workflow

    async def create_draft(
        self, actor: User, payload: ListingDraftCreate
    ) -> IntegrationListingRead:
        self.auth.require(MANAGE_PERMISSION)
        manifest = validate_manifest(payload.manifest)
        validate_auth_method(payload.auth_method)
        if not is_known_category(payload.category):
            raise AppError(f"Unknown integration category '{payload.category}'.")
        if await self.listings.get_by_key(self.auth.organization_id, manifest.key):
            raise AppError(
                f"A listing with key '{manifest.key}' already exists."
            )

        listing = IntegrationListing(
            publisher_organization_id=self.auth.organization_id,
            created_by=actor.id,
            is_first_party=False,
            certification="community",
            status="draft",
        )
        self._apply_manifest(listing, manifest, payload)
        self.session.add(listing)
        await self.session.flush()
        await self.session.refresh(listing)
        await self._audit_listing(
            AuditAction.MARKETPLACE_LISTING_CREATED, actor, listing
        )
        return _listing_read(listing, installed=False)

    async def submit(self, actor: User, listing_id: UUID) -> IntegrationListingRead:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load_own(listing_id)
        listing.status = self._transition(listing, "submit")
        await self.session.flush()
        await self.session.refresh(listing)
        await self._audit_listing(
            AuditAction.MARKETPLACE_LISTING_SUBMITTED, actor, listing
        )
        return _listing_read(listing, installed=False)

    async def review(
        self, actor: User, listing_id: UUID, decision: str, evidence: str | None
    ) -> IntegrationListingRead:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load_own(listing_id)
        if decision == "approved":
            listing.status = self._transition(listing, "approve")
            action = AuditAction.MARKETPLACE_LISTING_APPROVED
        elif decision == "rejected":
            listing.status = self._transition(listing, "reject")
            action = AuditAction.MARKETPLACE_LISTING_REJECTED
        else:
            raise AppError("Review decision must be 'approved' or 'rejected'.")

        self.session.add(
            IntegrationReview(
                organization_id=self.auth.organization_id,
                listing_id=listing.id,
                reviewer_id=actor.id,
                decision=decision,
                evidence=evidence,
            )
        )
        await self.session.flush()
        await self.session.refresh(listing)
        await self._audit_listing(action, actor, listing)
        return _listing_read(listing, installed=False)

    async def publish(self, actor: User, listing_id: UUID) -> IntegrationListingRead:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load_own(listing_id)
        listing.status = self._transition(listing, "publish")
        await self._provision_plugin(actor, listing)
        await self._ensure_version(
            actor, listing, str(listing.manifest.get("version") or "1.0.0"), {}
        )
        await self.session.flush()
        await self.session.refresh(listing)
        await self._audit_listing(
            AuditAction.MARKETPLACE_LISTING_PUBLISHED, actor, listing
        )
        return _listing_read(listing, installed=False)

    # ------------------------------------------------ version management

    async def create_version(
        self, actor: User, listing_id: UUID, version: str, compatibility: dict[str, object]
    ) -> IntegrationVersionRead:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load_own(listing_id)
        if await self.versions.get_by_version(
            listing.id, version, self.auth.organization_id
        ):
            raise AppError(f"Version '{version}' already exists for this listing.")
        newest = await self._latest_published(listing.id)
        if newest is not None:
            try:
                newer = is_newer(version, newest.version)
            except ValueError as exc:
                raise AppError(f"Invalid version '{version}'.") from exc
            if not newer:
                raise AppError(
                    f"Version '{version}' is not newer than '{newest.version}'."
                )
        row = await self._ensure_version(actor, listing, version, compatibility)
        return self._version_read(listing.key, row)

    async def list_versions(self, listing_id: UUID) -> list[IntegrationVersionRead]:
        listing = await self._load(listing_id)
        rows = await self.versions.list_for_listing(
            listing.id, self.auth.organization_id
        )
        return [self._version_read(listing.key, row) for row in rows]

    async def check_compatibility(self, listing_id: UUID) -> CompatibilityResultRead:
        listing = await self._load(listing_id)
        newest = await self._latest_published(listing.id)
        version = newest.version if newest else str(
            listing.manifest.get("version") or "1.0.0"
        )
        meta = dict(newest.compatibility) if newest else {}
        result = check_compatibility(version, meta)
        return CompatibilityResultRead(
            listing_key=listing.key,
            version=result.version,
            compatible=result.compatible,
            reason=result.reason,
        )

    # ------------------------------------------------ operational installs

    async def install(
        self, actor: User, listing_id: UUID, payload: InstallListingRequest
    ) -> InstalledIntegrationRead:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load(listing_id)
        if not is_installable(listing.status):
            raise AppError(
                f"This integration is '{listing.status}' and cannot be installed."
            )
        newest = await self._latest_published(listing.id)
        version = newest.version if newest else str(
            listing.manifest.get("version") or "1.0.0"
        )
        if newest is not None:
            compat = check_compatibility(newest.version, dict(newest.compatibility))
            if not compat.compatible:
                raise AppError(
                    f"Version '{newest.version}' is not compatible: {compat.reason}."
                )

        # Delegate the runtime install to the 9.2 service (plugin platform).
        installed = await IntegrationInstallationService(
            self.session, self.auth, self.settings
        ).install(actor, listing_id, payload)

        # Record the operational install-history row.
        self.session.add(
            IntegrationInstallation(
                organization_id=self.auth.organization_id,
                installed_by=actor.id,
                listing_id=listing.id,
                installed_plugin_id=installed.plugin_id,
                installed_version=version,
                status="active",
            )
        )
        await self.session.flush()
        return installed

    async def uninstall(self, actor: User, listing_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        listing = await self._load(listing_id)
        record = await self.installs.get_active(
            self.auth.organization_id, listing.id
        )
        if record is not None:
            record.status = next_install_state(record.status, "uninstall")
            record.uninstalled_at = _now()
            await self.session.flush()

        # Delegate the runtime uninstall to the 9.2 service (plugin platform).
        await IntegrationInstallationService(
            self.session, self.auth, self.settings
        ).uninstall(actor, listing_id)
        await self.audit.record(
            action=AuditAction.MARKETPLACE_INTEGRATION_UNINSTALLED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="integration_listing",
            entity_id=listing.id,
            metadata={"listing_key": listing.key},
        )

    async def list_installed(self) -> list[OperationalInstallationRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.installs.list_active_for_org(self.auth.organization_id)
        return await self._installation_reads(rows)

    async def installation_history(self) -> list[OperationalInstallationRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.installs.history_for_org(self.auth.organization_id)
        return await self._installation_reads(rows)

    # ------------------------------------------------ upgrade foundation

    async def upgrade_readiness(self, listing_id: UUID) -> UpgradeReadinessRead:
        """Read-only: whether an installed integration can move to a newer
        version. No side effects."""
        self.auth.require(MANAGE_PERMISSION)
        listing, readiness = await self._compute_upgrade(listing_id)
        return self._readiness_read(listing, readiness)

    async def prepare_upgrade(
        self, actor: User, listing_id: UUID
    ) -> UpgradeReadinessRead:
        """Begin an upgrade: compute the readiness and, when an upgrade is
        available and compatible, record that it was started. The runtime swap
        itself is deliberately out of scope — this is the upgrade foundation."""
        self.auth.require(MANAGE_PERMISSION)
        listing, readiness = await self._compute_upgrade(listing_id)
        if readiness.upgrade_available and readiness.compatible:
            await self.audit.record(
                action=AuditAction.MARKETPLACE_INTEGRATION_UPGRADE_STARTED,
                organization_id=self.auth.organization_id,
                actor_id=actor.id,
                actor_email=actor.email,
                entity_type="integration_listing",
                entity_id=listing.id,
                metadata={
                    "listing_key": listing.key,
                    "from": readiness.installed_version,
                    "to": readiness.latest_version,
                },
            )
        return self._readiness_read(listing, readiness)

    async def _compute_upgrade(
        self, listing_id: UUID
    ) -> tuple[IntegrationListing, UpgradeReadiness]:
        listing = await self._load(listing_id)
        record = await self.installs.get_active(
            self.auth.organization_id, listing.id
        )
        if record is None:
            raise NotFoundError("This integration is not installed.")
        newest = await self._latest_published(listing.id)
        latest_version = newest.version if newest else record.installed_version
        meta = dict(newest.compatibility) if newest else {}
        compat = check_compatibility(latest_version, meta)
        return listing, assess_upgrade(record.installed_version, latest_version, compat)

    def _readiness_read(
        self, listing: IntegrationListing, readiness: UpgradeReadiness
    ) -> UpgradeReadinessRead:
        return UpgradeReadinessRead(
            listing_key=listing.key,
            installed_version=readiness.installed_version,
            latest_version=readiness.latest_version,
            upgrade_available=readiness.upgrade_available,
            compatible=readiness.compatible,
            reason=readiness.reason,
        )

    # ------------------------------------------------ analytics

    async def analytics(self) -> MarketplaceAnalyticsRead:
        self.auth.require(MANAGE_PERMISSION)
        org = self.auth.organization_id
        raw = await self.listings.count_by_status(org)
        by_state = summarize_states(
            [state for state, count in raw.items() for _ in range(count)],
            LISTING_STATES,
        )
        total = await self.listings.count_visible(org)
        active_installs = await self.installs.count_active(org)
        history = await self.installs.history_for_org(org)

        counts = await self.installs.count_active_by_listing(org)
        key_by_id = {row.id: row.key for row in await self.listings.discover(org)}
        key_counts = {
            key_by_id[listing_id]: count
            for listing_id, count in counts.items()
            if listing_id in key_by_id
        }
        adoption = [
            AdoptionRead(
                listing_key=metric.listing_key,
                active_installs=metric.active_installs,
                signal=metric.signal,
            )
            for metric in adoption_metrics(key_counts)
        ]
        return MarketplaceAnalyticsRead(
            total_listings=total,
            listings_by_state=by_state,
            published=by_state["published"],
            active_installs=active_installs,
            total_installs=len(history),
            operational_health=operational_health(
                by_state["published"], active_installs
            ),
            adoption=adoption,
        )

    # ------------------------------------------------ helpers

    async def _latest_published(
        self, listing_id: UUID
    ) -> IntegrationVersion | None:
        """The newest published version of a listing, by semver.

        Chosen by version rather than ``created_at`` on purpose: rows cut in the
        same transaction share ``now()``, so a timestamp sort cannot tell them
        apart. Semver ordering always can.
        """
        rows = [
            row
            for row in await self.versions.list_for_listing(
                listing_id, self.auth.organization_id
            )
            if row.status == "published"
        ]
        if not rows:
            return None
        newest_version = latest([row.version for row in rows])
        return next(
            (row for row in rows if row.version == newest_version), rows[0]
        )

    def _transition(self, listing: IntegrationListing, action: str) -> str:
        try:
            return next_listing_state(listing.status, action)
        except PublicationError as exc:
            raise AppError(str(exc)) from exc

    async def _provision_plugin(self, actor: User, listing: IntegrationListing) -> None:
        from app.services.plugin import PluginRegistryService

        existing = await self.plugins.get_by_key(
            self.auth.organization_id, listing.key
        )
        if existing is not None:
            return
        registry = PluginRegistryService(self.session, self.auth)
        if listing.publisher_organization_id is None:
            await registry.ensure_global_plugin(actor, dict(listing.manifest))
        else:
            # A tenant-authored listing provisions a private plugin.
            await registry.publish(actor, dict(listing.manifest))

    async def _ensure_version(
        self,
        actor: User,
        listing: IntegrationListing,
        version: str,
        compatibility: dict[str, object],
    ) -> IntegrationVersion:
        existing = await self.versions.get_by_version(
            listing.id, version, self.auth.organization_id
        )
        if existing is not None:
            return existing
        row = IntegrationVersion(
            publisher_organization_id=listing.publisher_organization_id,
            created_by=actor.id,
            listing_id=listing.id,
            version=version,
            manifest=dict(listing.manifest),
            compatibility=compatibility or {"sdk_version": SDK_VERSION},
            status="published",
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def _installation_reads(
        self, rows: Sequence[IntegrationInstallation]
    ) -> list[OperationalInstallationRead]:
        key_by_id = {
            row.id: row.key
            for row in await self.listings.discover(self.auth.organization_id)
        }
        reads: list[OperationalInstallationRead] = []
        for record in rows:
            reads.append(
                OperationalInstallationRead(
                    id=record.id,
                    listing_key=key_by_id.get(record.listing_id, "unknown"),
                    installed_version=record.installed_version,
                    status=record.status,
                    installed_plugin_id=record.installed_plugin_id,
                    installed_at=record.installed_at,
                    uninstalled_at=record.uninstalled_at,
                )
            )
        return reads

    def _version_read(
        self, listing_key: str, row: IntegrationVersion
    ) -> IntegrationVersionRead:
        return IntegrationVersionRead(
            id=row.id,
            listing_key=listing_key,
            version=row.version,
            status=row.status,
            compatibility=dict(row.compatibility),
            created_at=row.created_at,
        )

    def _apply_manifest(
        self,
        listing: IntegrationListing,
        manifest: PluginManifest,
        payload: ListingDraftCreate,
    ) -> None:
        listing.key = manifest.key
        listing.name = manifest.name
        listing.vendor = payload.vendor
        listing.category = payload.category
        listing.summary = payload.summary
        listing.description = manifest.description
        listing.auth_method = payload.auth_method
        listing.oauth_scopes = list(payload.oauth_scopes)
        listing.provider_key = payload.provider_key or manifest.provider_key
        listing.manifest = manifest.model_dump()
        listing.capabilities = list(manifest.capabilities)
        listing.event_types = list(manifest.events)
        listing.required_feature = manifest.required_feature
        listing.docs_url = payload.docs_url

    async def _load(self, listing_id: UUID) -> IntegrationListing:
        listing = await self.listings.get_visible(
            listing_id, self.auth.organization_id
        )
        if listing is None:
            raise NotFoundError("Integration listing not found.")
        return listing

    async def _load_own(self, listing_id: UUID) -> IntegrationListing:
        listing = await self._load(listing_id)
        if listing.publisher_organization_id != self.auth.organization_id:
            raise AppError("A curated listing cannot be modified by a tenant.")
        return listing

    async def _audit_listing(
        self, action: str, actor: User, listing: IntegrationListing
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="integration_listing",
            entity_id=listing.id,
            metadata={"key": listing.key, "status": listing.status},
        )


__all__ = ["MANAGE_PERMISSION", "MarketplaceOperationsService"]
