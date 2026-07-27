"""API key service — issuance, rotation, revocation, and machine authentication.

The two halves are deliberately separate. **Management** (create / list / rotate
/ revoke) runs inside an ordinary tenant-bound user session and is gated on
`settings.manage`, with one extra rule that keeps the system safe: a key can
never out-grant the person who mints it. Each requested scope is bounded to a
subset of the creator's own grants at creation, and bounded again against the
creator's *current* grants at every authentication — so if the creator loses a
permission, the key loses it too.

**Authentication** is the machine counterpart of login. It is handed an opaque
key with no tenant context, resolves the tenant through the SECURITY DEFINER
lookup (RLS denies the row otherwise), reads the key under RLS, and produces an
`AuthorizationContext` marked as a machine principal (`api_key_id` set, the
creator as the audit actor and scope anchor). It never widens authority, takes
no action on CRM data, and mints nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import (
    AppError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.logging import get_logger
from app.core.permissions import PERMISSIONS_BY_KEY, Scope
from app.core.security import API_KEY_PREFIX, create_api_key, hash_api_key
from app.db.session import set_tenant_context
from app.models.api_key import KEY_ENVIRONMENTS, ApiKey
from app.models.user import User
from app.repositories.api_key import ApiKeyRepository
from app.repositories.user import UserRepository
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext, RbacService

logger = get_logger(__name__)

#: Managing machine credentials is an administrative act — it grants a slice of
#: someone's authority to a non-interactive principal — so it rides on the same
#: permission as changing workspace settings.
MANAGE_PERMISSION = "settings.manage"

ENTITY_TYPE = "api_key"

# One generic message for every authentication failure, so a caller cannot
# distinguish "no such key" from "revoked" from "creator suspended".
_AUTH_ERROR = "API key is not valid."


class ApiKeyService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repo = ApiKeyRepository(session)
        self.audit = AuditService(session)

    # -------------------------------------------------------- management

    async def create(
        self,
        auth: AuthorizationContext,
        actor: User,
        *,
        name: str,
        scopes: dict[str, str],
        expires_in_days: int | None,
        environment: str = "live",
    ) -> tuple[ApiKey, str]:
        """Mint a key. Returns the row and the raw secret (shown once).

        `environment` is `live` or `sandbox` (Phase 7.6). A sandbox key is a
        developer test credential: it skips the plan gate and the API-key quota,
        so it can be minted on any plan without spending a paid slot.
        """
        auth.require(MANAGE_PERMISSION)
        environment = self._resolve_environment(environment)
        if environment == "live":
            await self._enforce_billing(auth)
        bounded = self._bound_scopes(auth, scopes)

        generated = create_api_key()
        key = ApiKey(
            organization_id=auth.organization_id,
            created_by=actor.id,
            name=name,
            environment=environment,
            token_hash=generated.token_hash,
            prefix=generated.prefix,
            last_four=generated.last_four,
            scopes=bounded,
            expires_at=self._resolve_expiry(expires_in_days),
        )
        self.session.add(key)
        await self.session.flush()

        await self._audit(AuditAction.API_KEY_CREATED, auth, actor, key)
        logger.info(
            "api_key_created",
            extra={"api_key_id": str(key.id), "actor_id": str(actor.id)},
        )
        return key, generated.raw

    async def list_keys(self, auth: AuthorizationContext) -> list[ApiKey]:
        auth.require(MANAGE_PERMISSION)
        return list(await self.repo.list_for_org(auth.organization_id))

    async def rotate(
        self, auth: AuthorizationContext, actor: User, key_id: UUID
    ) -> tuple[ApiKey, str]:
        """Issue a fresh secret for a key, invalidating the previous one.

        Same id, same scopes, same expiry — only the secret changes, so a
        compromised key is remediated without reconfiguring the client's grants.
        """
        auth.require(MANAGE_PERMISSION)
        key = await self._load(auth, key_id)
        if key.revoked_at is not None:
            raise ConflictError("A revoked key cannot be rotated.")

        generated = create_api_key()
        key.token_hash = generated.token_hash
        key.prefix = generated.prefix
        key.last_four = generated.last_four
        await self.session.flush()

        await self._audit(AuditAction.API_KEY_ROTATED, auth, actor, key)
        logger.info("api_key_rotated", extra={"api_key_id": str(key.id)})
        return key, generated.raw

    async def revoke(
        self, auth: AuthorizationContext, actor: User, key_id: UUID
    ) -> ApiKey:
        """Revoke a key. Idempotent — revoking a revoked key is a no-op."""
        auth.require(MANAGE_PERMISSION)
        key = await self._load(auth, key_id)
        if key.revoked_at is not None:
            return key

        key.revoked_at = datetime.now(UTC)
        key.revoked_by = actor.id
        await self.session.flush()

        await self._audit(AuditAction.API_KEY_REVOKED, auth, actor, key)
        logger.info("api_key_revoked", extra={"api_key_id": str(key.id)})
        return key

    # ---------------------------------------------------- authentication

    async def authenticate(self, raw_key: str) -> AuthorizationContext:
        """Resolve a raw API key to a machine `AuthorizationContext`.

        Binds the tenant context on this session as a side effect (the request
        transaction then runs under RLS for the key's org), exactly as login
        does for a user. Every failure raises the same generic error.
        """
        if not raw_key or not raw_key.startswith(API_KEY_PREFIX):
            raise AuthenticationError(_AUTH_ERROR)

        token_hash = hash_api_key(raw_key)
        organization_id = await self.repo.lookup_organization(token_hash)
        if organization_id is None:
            raise AuthenticationError(_AUTH_ERROR)

        await set_tenant_context(self.session, organization_id)

        key = await self.repo.get_by_hash(token_hash)
        if key is None or not key.is_active or key.created_by is None:
            raise AuthenticationError(_AUTH_ERROR)

        # The key can never exceed its creator's *current* authority, so the
        # creator must still be a live member of the workspace.
        creator = await UserRepository(self.session).get_with_organization(
            key.created_by, organization_id
        )
        if creator is None or not creator.is_active:
            raise AuthenticationError(_AUTH_ERROR)

        resolved = await RbacService(self.session).resolve(
            key.created_by, organization_id
        )
        effective = _effective_grants(key.scopes, resolved.grants)

        await self.repo.touch_last_used(key)

        logger.info(
            "api_key_authenticated",
            extra={
                "api_key_id": str(key.id),
                "organization_id": str(organization_id),
                "creator_id": str(key.created_by),
                "environment": key.environment,
            },
        )
        return AuthorizationContext(
            user_id=key.created_by,
            organization_id=organization_id,
            role_keys=("api_key",),
            grants=effective,
            api_key_id=key.id,
            api_key_environment=key.environment,
        )

    # ---------------------------------------------------------- helpers

    async def _enforce_billing(self, auth: AuthorizationContext) -> None:
        """Plan gate on minting a key, when billing enforcement is on.

        A no-op unless `BILLING_ENFORCED` — so a deployment not using billing,
        and every existing test, is unaffected. When on, the tenant's plan must
        grant API access and have an API-key quota left. Imported lazily to keep
        the billing package off the auth import path.
        """
        if not self.settings.BILLING_ENFORCED:
            return
        from app.services.billing.service import EntitlementService, QuotaService

        await EntitlementService(self.session, auth).require("api_access")
        keys = await self.repo.list_for_org(auth.organization_id)
        # Sandbox keys are free test credentials — only live keys count.
        active = sum(1 for key in keys if key.is_active and not key.is_sandbox)
        await QuotaService(self.session, auth).enforce(
            "api_keys", active, adding=1, resource="API keys"
        )

    def _resolve_environment(self, environment: str) -> str:
        if environment not in KEY_ENVIRONMENTS:
            raise AppError(
                f"Unknown environment '{environment}'. "
                f"Use one of: {', '.join(KEY_ENVIRONMENTS)}."
            )
        return environment

    async def _load(self, auth: AuthorizationContext, key_id: UUID) -> ApiKey:
        key = await self.repo.get(key_id, auth.organization_id)
        if key is None:
            raise NotFoundError("API key not found.")
        return key

    def _bound_scopes(
        self, auth: AuthorizationContext, requested: dict[str, str]
    ) -> dict[str, str]:
        """Validate and bound the requested scopes to the creator's authority.

        A creator cannot grant a permission they do not hold, nor a scope wider
        than their own — otherwise a key would be an escalation path around RBAC.
        """
        bounded: dict[str, str] = {}
        for permission, scope_value in requested.items():
            if permission not in PERMISSIONS_BY_KEY:
                raise AppError(f"Unknown permission '{permission}'.")
            try:
                requested_scope = Scope(scope_value)
            except ValueError as exc:
                raise AppError(f"Unknown scope '{scope_value}'.") from exc

            held = auth.grants.get(permission)
            if held is None:
                raise PermissionDeniedError(
                    f"You cannot grant '{permission}' — you do not hold it."
                )
            if requested_scope.rank > held.rank:
                raise PermissionDeniedError(
                    f"You cannot grant '{permission}' at a wider scope than your own."
                )
            bounded[permission] = requested_scope.value
        return bounded

    def _resolve_expiry(self, expires_in_days: int | None) -> datetime:
        """Every key expires. An omitted lifetime defaults to the workspace
        maximum, and a requested one is capped to it — a leaked key is bounded
        either way, and forced rotation is the point of the ceiling."""
        cap = self.settings.API_KEY_MAX_TTL_DAYS
        days = cap if expires_in_days is None else min(expires_in_days, cap)
        return datetime.now(UTC) + timedelta(days=days)

    async def _audit(
        self,
        action: str,
        auth: AuthorizationContext,
        actor: User,
        key: ApiKey,
    ) -> None:
        await self.audit.record(
            action=action,
            organization_id=auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type=ENTITY_TYPE,
            entity_id=key.id,
            metadata={
                "name": key.name,
                "prefix": key.prefix,
                "environment": key.environment,
                "scopes": dict(key.scopes),
                "expires_at": key.expires_at,
            },
        )


def _effective_grants(
    stored: dict[str, object], creator_grants: dict[str, Scope]
) -> dict[str, Scope]:
    """The key's live grants: its stored scopes, each capped at the creator's
    current scope, and dropped entirely where the creator no longer holds it."""
    effective: dict[str, Scope] = {}
    for permission, scope_value in stored.items():
        held = creator_grants.get(permission)
        if held is None:
            continue
        stored_scope = Scope(str(scope_value))
        effective[permission] = (
            stored_scope if stored_scope.rank <= held.rank else held
        )
    return effective


__all__ = ["MANAGE_PERMISSION", "ApiKeyService"]
