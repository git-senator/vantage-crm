"""App marketplace & plugin platform services.

The orchestration around the deterministic plugin core. It publishes and syncs
catalog plugins (validating each manifest), installs them per tenant, moves each
installation through its lifecycle, stores configuration (sealing secrets), wires
event subscriptions, and exposes the backend extension seam future integrations
plug into.

Reuse is the rule. Capabilities map onto existing RBAC permissions; a manifest's
``required_feature`` is checked through the existing feature-flag service; the
subscribable events are the webhook vocabulary; secrets are sealed with the same
``SecretBox`` that seals every other credential; and every act is written to the
audit log. Every management method is gated on ``settings.manage`` under RLS;
browsing the catalog needs only an authenticated tenant member.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.core.secrets import get_secret_box
from app.models.plugin import Plugin, PluginEventSubscription, PluginInstallation
from app.models.user import User
from app.plugins.capabilities import PLUGIN_CAPABILITIES, capability
from app.plugins.catalog import FIRST_PARTY_PLUGINS
from app.plugins.lifecycle import next_state
from app.plugins.manifest import (
    PLUGIN_CATEGORIES,
    PLUGIN_EVENT_TYPES,
    PluginManifest,
    validate_manifest,
)
from app.repositories.plugin import (
    PluginEventSubscriptionRepository,
    PluginInstallationRepository,
    PluginRepository,
)
from app.schemas.plugin import (
    InstallationRead,
    InstallRequest,
    MarketplaceDashboard,
    PluginRead,
    SubscriptionRead,
)
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext

MANAGE_PERMISSION = "settings.manage"
_SECRET_CONTEXT = "plugin_secret"  # noqa: S105  # AAD label, not a credential


def _now() -> datetime:
    return datetime.now(UTC)


def _secret_keys(plugin: Plugin) -> set[str]:
    return {
        f["key"]
        for f in plugin.config_schema
        if isinstance(f, dict) and f.get("type") == "secret"
    }


# =========================================================== catalog / registry


class PluginRegistryService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = PluginRepository(session)
        self.audit = AuditService(session)

    async def list_catalog(
        self, *, category: str | None = None
    ) -> list[PluginRead]:
        """Marketplace browse: first-party plugins plus this tenant's own.
        Available to any authenticated member — no management gate."""
        rows = await self.repo.list_catalog(
            self.auth.organization_id, category=category
        )
        return [_plugin_read(row) for row in rows]

    async def get(self, plugin_id: UUID) -> PluginRead:
        return _plugin_read(await self._load(plugin_id))

    async def publish(self, actor: User, manifest_data: dict[str, Any]) -> PluginRead:
        self.auth.require(MANAGE_PERMISSION)
        manifest = validate_manifest(manifest_data)
        # Pre-check the visible catalog (own + first-party): the common collision
        # is a clean 400 with the session intact. The unique constraint below is
        # the backstop for a key held privately by another tenant, which RLS
        # hides from this pre-check.
        if await self.repo.get_by_key(self.auth.organization_id, manifest.key):
            raise AppError(f"A plugin with key '{manifest.key}' already exists.")
        plugin = _plugin_from_manifest(
            manifest,
            publisher_organization_id=self.auth.organization_id,
            created_by=actor.id,
            is_first_party=False,
        )
        self.session.add(plugin)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise AppError(f"A plugin with key '{manifest.key}' already exists.") from exc
        await self.session.refresh(plugin)
        await self._audit(AuditAction.PLUGIN_PUBLISHED, actor, plugin)
        return _plugin_read(plugin)

    async def deprecate(self, actor: User, plugin_id: UUID) -> PluginRead:
        self.auth.require(MANAGE_PERMISSION)
        plugin = await self._load_own(plugin_id)
        plugin.status = "deprecated"
        await self.session.flush()
        await self._audit(AuditAction.PLUGIN_DEPRECATED, actor, plugin)
        return _plugin_read(plugin)

    async def sync_first_party(self, actor: User) -> int:
        """Upsert the shipped first-party plugins as global catalog entries.
        Idempotent: an existing first-party plugin is refreshed, not duplicated."""
        self.auth.require(MANAGE_PERMISSION)
        synced = 0
        for data in FIRST_PARTY_PLUGINS:
            manifest = validate_manifest(dict(data))
            existing = await self.repo.get_by_key(
                self.auth.organization_id, manifest.key
            )
            if existing is not None:
                if existing.publisher_organization_id is not None:
                    # A tenant already owns this key; do not clobber it.
                    continue
                _apply_manifest(existing, manifest)
            else:
                plugin = _plugin_from_manifest(
                    manifest,
                    publisher_organization_id=None,
                    created_by=actor.id,
                    is_first_party=True,
                )
                self.session.add(plugin)
            synced += 1
        await self.session.flush()
        return synced

    async def ensure_global_plugin(
        self, actor: User, manifest_data: dict[str, Any]
    ) -> Plugin:
        """Provision a first-party (global, publisher-less) plugin from a manifest,
        idempotently, and return it.

        The integration marketplace (Phase 9.2) calls this to stand up the plugin
        behind a curated listing. It is non-clobbering: if the key already exists
        — whether a tenant owns it or an earlier sync created it — that row wins
        and is returned untouched, so a marketplace sync never overwrites a
        first-party plugin nor steals a tenant's private key. Only a genuinely
        new key inserts a global row.
        """
        manifest = validate_manifest(dict(manifest_data))
        existing = await self.repo.get_by_key(self.auth.organization_id, manifest.key)
        if existing is not None:
            return existing
        plugin = _plugin_from_manifest(
            manifest,
            publisher_organization_id=None,
            created_by=actor.id,
            is_first_party=True,
        )
        self.session.add(plugin)
        await self.session.flush()
        await self.session.refresh(plugin)
        return plugin

    async def _load(self, plugin_id: UUID) -> Plugin:
        plugin = await self.repo.get_visible(plugin_id, self.auth.organization_id)
        if plugin is None:
            raise NotFoundError("Plugin not found.")
        return plugin

    async def _load_own(self, plugin_id: UUID) -> Plugin:
        plugin = await self._load(plugin_id)
        if plugin.publisher_organization_id != self.auth.organization_id:
            raise AppError("A first-party plugin cannot be modified by a tenant.")
        return plugin

    async def _audit(self, action: str, actor: User, plugin: Plugin) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="plugin",
            entity_id=plugin.id,
            metadata={"key": plugin.key, "version": plugin.version},
        )


def _plugin_from_manifest(
    manifest: PluginManifest,
    *,
    publisher_organization_id: UUID | None,
    created_by: UUID | None,
    is_first_party: bool,
) -> Plugin:
    plugin = Plugin(
        publisher_organization_id=publisher_organization_id,
        created_by=created_by,
        is_first_party=is_first_party,
        status="published",
    )
    _apply_manifest(plugin, manifest)
    return plugin


def _apply_manifest(plugin: Plugin, manifest: PluginManifest) -> None:
    plugin.key = manifest.key
    plugin.name = manifest.name
    plugin.version = manifest.version
    plugin.description = manifest.description
    plugin.publisher_name = manifest.publisher
    plugin.category = manifest.category
    plugin.manifest = manifest.model_dump()
    plugin.capabilities = list(manifest.capabilities)
    plugin.event_types = list(manifest.events)
    plugin.config_schema = [f.model_dump() for f in manifest.config_schema]
    plugin.required_feature = manifest.required_feature
    plugin.provider_key = manifest.provider_key


def _plugin_read(row: Plugin) -> PluginRead:
    return PluginRead(
        id=row.id,
        key=row.key,
        name=row.name,
        version=row.version,
        description=row.description,
        publisher_name=row.publisher_name,
        category=row.category,
        capabilities=list(row.capabilities),
        event_types=list(row.event_types),
        config_schema=[dict(f) for f in row.config_schema],
        required_feature=row.required_feature,
        provider_key=row.provider_key,
        is_first_party=row.is_first_party,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# =========================================================== installations


class PluginInstallationService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.repo = PluginInstallationRepository(session)
        self.plugins = PluginRepository(session)
        self.subs = PluginEventSubscriptionRepository(session)
        self.audit = AuditService(session)
        self._box = get_secret_box()

    async def install(self, actor: User, payload: InstallRequest) -> InstallationRead:
        self.auth.require(MANAGE_PERMISSION)
        if await self.repo.count_for_org(self.auth.organization_id) >= (
            self.settings.PLUGINS_MAX_PER_TENANT
        ):
            raise AppError("Plugin install limit reached for this workspace.")

        plugin = await self.plugins.get_visible(
            payload.plugin_id, self.auth.organization_id
        )
        if plugin is None:
            raise NotFoundError("Plugin not found.")
        if plugin.status == "deprecated":
            raise AppError("This plugin is deprecated and cannot be installed.")
        if await self.repo.get_by_plugin(self.auth.organization_id, plugin.id):
            raise AppError("This plugin is already installed.")

        await self._require_feature(plugin)
        granted = self._resolve_capabilities(plugin, payload.granted_capabilities)

        installation = PluginInstallation(
            organization_id=self.auth.organization_id,
            installed_by=actor.id,
            plugin_id=plugin.id,
            status="installed",
            granted_capabilities=granted,
            config={},
            secrets={},
        )
        self._apply_config(installation, plugin, payload.config)
        self.session.add(installation)
        await self.session.flush()

        # Wire the manifest's declared event subscriptions; they only fire once
        # the installation is enabled (see the subscribers query).
        for event_type in plugin.event_types:
            self.session.add(
                PluginEventSubscription(
                    organization_id=self.auth.organization_id,
                    installation_id=installation.id,
                    event_type=event_type,
                )
            )
        await self.session.flush()
        await self.session.refresh(installation)
        await self._audit(AuditAction.PLUGIN_INSTALLED, actor, installation, plugin)
        return await self._read(installation, plugin)

    async def list_installations(
        self, *, status: str | None = None
    ) -> list[InstallationRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(self.auth.organization_id, status=status)
        return [await self._read(row) for row in rows]

    async def get(self, installation_id: UUID) -> InstallationRead:
        self.auth.require(MANAGE_PERMISSION)
        return await self._read(await self._load(installation_id))

    async def enable(self, actor: User, installation_id: UUID) -> InstallationRead:
        return await self._transition(
            actor, installation_id, action="enable",
            audit=AuditAction.PLUGIN_ENABLED,
        )

    async def disable(self, actor: User, installation_id: UUID) -> InstallationRead:
        return await self._transition(
            actor, installation_id, action="disable",
            audit=AuditAction.PLUGIN_DISABLED,
        )

    async def configure(
        self, actor: User, installation_id: UUID, config: dict[str, Any]
    ) -> InstallationRead:
        self.auth.require(MANAGE_PERMISSION)
        installation = await self._load(installation_id)
        plugin = await self._plugin_for(installation)
        self._apply_config(installation, plugin, config)
        await self.session.flush()
        await self.session.refresh(installation)
        await self._audit(
            AuditAction.PLUGIN_CONFIGURED, actor, installation, plugin
        )
        return await self._read(installation, plugin)

    async def uninstall(self, actor: User, installation_id: UUID) -> None:
        self.auth.require(MANAGE_PERMISSION)
        installation = await self._load(installation_id)
        plugin = await self._plugin_for(installation)
        await self._audit(
            AuditAction.PLUGIN_UNINSTALLED, actor, installation, plugin
        )
        await self.session.delete(installation)  # cascades subscriptions
        await self.session.flush()

    async def _transition(
        self, actor: User, installation_id: UUID, *, action: str, audit: str
    ) -> InstallationRead:
        self.auth.require(MANAGE_PERMISSION)
        installation = await self._load(installation_id)
        installation.status = next_state(installation.status, action)
        now = _now()
        if action == "enable":
            installation.enabled_at = now
        else:
            installation.disabled_at = now
        await self.session.flush()
        await self.session.refresh(installation)
        plugin = await self._plugin_for(installation)
        await self._audit(audit, actor, installation, plugin)
        return await self._read(installation, plugin)

    def _resolve_capabilities(
        self, plugin: Plugin, requested: list[str] | None
    ) -> list[str]:
        declared = set(plugin.capabilities)
        granted = declared if requested is None else set(requested)
        for cap in granted:
            capability(cap)  # registered?
            if cap not in declared:
                raise AppError(
                    f"Capability '{cap}' is not declared by this plugin."
                )
        return sorted(granted)

    def _apply_config(
        self, installation: PluginInstallation, plugin: Plugin, config: dict[str, Any]
    ) -> None:
        secret_keys = _secret_keys(plugin)
        new_config = dict(installation.config)
        new_secrets = dict(installation.secrets)
        for key, value in config.items():
            if key in secret_keys:
                new_secrets[key] = self._box.encrypt(
                    str(value), context=_SECRET_CONTEXT
                )
            else:
                new_config[key] = value
        installation.config = new_config
        installation.secrets = new_secrets

    async def _require_feature(self, plugin: Plugin) -> None:
        if not plugin.required_feature:
            return
        from app.services.enterprise import FeatureService

        enabled = await FeatureService(
            self.session, self.auth, self.settings
        ).has_feature(plugin.required_feature)
        if not enabled:
            raise AppError(
                f"This plugin requires the '{plugin.required_feature}' feature."
            )

    async def _load(self, installation_id: UUID) -> PluginInstallation:
        installation = await self.repo.get(
            installation_id, self.auth.organization_id
        )
        if installation is None:
            raise NotFoundError("Installation not found.")
        return installation

    async def _plugin_for(self, installation: PluginInstallation) -> Plugin:
        plugin = await self.plugins.get_visible(
            installation.plugin_id, self.auth.organization_id
        )
        if plugin is None:
            raise NotFoundError("Plugin not found.")
        return plugin

    async def _read(
        self, installation: PluginInstallation, plugin: Plugin | None = None
    ) -> InstallationRead:
        plugin = plugin or await self._plugin_for(installation)
        subs = await self.subs.list_for_installation(
            self.auth.organization_id, installation.id
        )
        return InstallationRead(
            id=installation.id,
            plugin_id=installation.plugin_id,
            plugin_key=plugin.key,
            plugin_name=plugin.name,
            status=installation.status,
            granted_capabilities=list(installation.granted_capabilities),
            config=dict(installation.config),
            secret_keys=sorted(installation.secrets.keys()),
            subscriptions=[s.event_type for s in subs],
            enabled_at=installation.enabled_at,
            disabled_at=installation.disabled_at,
            created_at=installation.created_at,
            updated_at=installation.updated_at,
        )

    async def _audit(
        self,
        action: str,
        actor: User,
        installation: PluginInstallation,
        plugin: Plugin,
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="plugin_installation",
            entity_id=installation.id,
            metadata={"plugin_key": plugin.key, "status": installation.status},
        )


# =========================================================== event subscriptions


class PluginEventService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = PluginEventSubscriptionRepository(session)
        self.installs = PluginInstallationRepository(session)
        self.plugins = PluginRepository(session)
        self.audit = AuditService(session)

    async def list_subscriptions(
        self, installation_id: UUID
    ) -> list[SubscriptionRead]:
        self.auth.require(MANAGE_PERMISSION)
        await self._require_installation(installation_id)
        rows = await self.repo.list_for_installation(
            self.auth.organization_id, installation_id
        )
        return [_subscription_read(row) for row in rows]

    async def subscribe(
        self, actor: User, installation_id: UUID, event_type: str
    ) -> SubscriptionRead:
        self.auth.require(MANAGE_PERMISSION)
        installation = await self._require_installation(installation_id)
        plugin = await self.plugins.get_visible(
            installation.plugin_id, self.auth.organization_id
        )
        if event_type not in PLUGIN_EVENT_TYPES:
            raise AppError(f"'{event_type}' is not a subscribable event.")
        if plugin is not None and event_type not in plugin.event_types:
            raise AppError(
                f"This plugin does not declare the '{event_type}' event."
            )
        if await self.repo.find(
            self.auth.organization_id, installation_id, event_type
        ):
            raise AppError("Already subscribed to this event.")

        subscription = PluginEventSubscription(
            organization_id=self.auth.organization_id,
            installation_id=installation_id,
            event_type=event_type,
        )
        self.session.add(subscription)
        await self.session.flush()
        await self._audit(
            AuditAction.PLUGIN_SUBSCRIBED, actor, installation_id, event_type
        )
        return _subscription_read(subscription)

    async def unsubscribe(
        self, actor: User, installation_id: UUID, event_type: str
    ) -> None:
        self.auth.require(MANAGE_PERMISSION)
        await self._require_installation(installation_id)
        subscription = await self.repo.find(
            self.auth.organization_id, installation_id, event_type
        )
        if subscription is None:
            raise NotFoundError("Subscription not found.")
        await self._audit(
            AuditAction.PLUGIN_UNSUBSCRIBED, actor, installation_id, event_type
        )
        await self.session.delete(subscription)
        await self.session.flush()

    async def _require_installation(
        self, installation_id: UUID
    ) -> PluginInstallation:
        installation = await self.installs.get(
            installation_id, self.auth.organization_id
        )
        if installation is None:
            raise NotFoundError("Installation not found.")
        return installation

    async def _audit(
        self, action: str, actor: User, installation_id: UUID, event_type: str
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="plugin_event_subscription",
            entity_id=installation_id,
            metadata={"event_type": event_type},
        )


def _subscription_read(row: PluginEventSubscription) -> SubscriptionRead:
    return SubscriptionRead(
        id=row.id,
        installation_id=row.installation_id,
        event_type=row.event_type,
        created_at=row.created_at,
    )


# =============================================== extension seam (backend)


class PluginExtensionService:
    """The backend seam future integrations dispatch through.

    Not an HTTP surface: CRM services and the event pipeline call this to ask
    "which enabled plugins care about this event/capability?". It operates under
    the caller's tenant session and RLS, and reuses the existing RBAC grants to
    decide whether a capability may actually be exercised.
    """

    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.installs = PluginInstallationRepository(session)
        self.subs = PluginEventSubscriptionRepository(session)

    async def enabled_installations(
        self, *, capability_key: str | None = None
    ) -> list[PluginInstallation]:
        rows = await self.installs.list_for_org(
            self.auth.organization_id, status="enabled"
        )
        if capability_key is None:
            return list(rows)
        return [r for r in rows if capability_key in r.granted_capabilities]

    def can_invoke(self, capability_key: str) -> bool:
        """Whether the current authorization context may exercise a capability —
        the RBAC reuse. A plugin can never do through the platform what its
        installer's grants do not already allow."""
        cap = capability(capability_key)
        return cap.required_permission in self.auth.grants

    async def subscribers(self, event_type: str) -> list[PluginInstallation]:
        rows = await self.subs.subscribers(self.auth.organization_id, event_type)
        return list(rows)

    async def dispatch_event(
        self, event_type: str, payload: dict[str, Any]
    ) -> int:
        """Fan an event out to the enabled installations subscribed to it, and
        return how many matched. Delivery itself rides the existing webhook
        rails; this is the subscription match the pipeline consults."""
        targets = await self.subscribers(event_type)
        return len(targets)


# =============================================== dashboard & catalogues


class MarketplaceDashboardService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.plugins = PluginRepository(session)
        self.installs = PluginInstallationRepository(session)
        self.subs = PluginEventSubscriptionRepository(session)

    async def dashboard(self) -> MarketplaceDashboard:
        self.auth.require(MANAGE_PERMISSION)
        org = self.auth.organization_id
        catalog = await self.plugins.list_catalog(org)
        by_status = await self.installs.count_by_status(org)
        installed = await self.installs.count_for_org(org)
        subscriptions = await self.subs.count_for_org(org)
        return MarketplaceDashboard(
            catalog_available=len(list(catalog)),
            installed=installed,
            enabled=by_status.get("enabled", 0),
            disabled=by_status.get("disabled", 0),
            subscriptions=subscriptions,
            by_status=by_status,
        )


def capabilities_catalogue() -> list[dict[str, object]]:
    return [
        {
            "key": c.key,
            "title": c.title,
            "description": c.description,
            "required_permission": c.required_permission,
        }
        for c in PLUGIN_CAPABILITIES.values()
    ]


def plugin_categories() -> list[str]:
    return list(PLUGIN_CATEGORIES)


__all__ = [
    "MANAGE_PERMISSION",
    "MarketplaceDashboardService",
    "PluginEventService",
    "PluginExtensionService",
    "PluginInstallationService",
    "PluginRegistryService",
    "capabilities_catalogue",
    "plugin_categories",
]
