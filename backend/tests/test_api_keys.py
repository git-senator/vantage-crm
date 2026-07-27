"""API keys — machine credentials, hashing, scope bounding, and authentication.

Phase 7.1. The properties that carry the milestone:

  * **Only a hash is stored.** The raw key never touches the database; a key is
    looked up by SHA-256, like a refresh token.
  * **A key cannot out-grant its creator.** Requested scopes are bounded to the
    creator's own at creation, and re-bounded to their current grants at every
    authentication.
  * **Authentication yields a machine principal.** `AuthorizationContext` with
    `api_key_id` set, the creator as the audit actor and scope anchor.
  * **Revocation and expiry are honoured**, and **tenant isolation holds** — a
    key is invisible to another workspace.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_api_key
from app.core.config import Settings
from app.core.exceptions import (
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.permissions import Scope
from app.core.security import API_KEY_PREFIX, create_api_key, hash_api_key
from app.models.api_key import ApiKey
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.services.api_key import ApiKeyService, _effective_grants
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

pytestmark = pytest.mark.integration

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "JWT_SECRET": SecretStr(TEST_JWT_SECRET),
        "ENVIRONMENT": "test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _auth(organization: Organization, user_id, **grants: Scope) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    resolved = {
        "settings.manage": Scope.ALL,
        "leads.view": Scope.ALL,
        "leads.manage": Scope.ALL,
        **grants,
    }
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants=resolved,
    )


# ------------------------------------------------------- pure helpers


class TestKeyGeneration:
    def test_key_has_prefix_and_hash_is_not_the_raw(self) -> None:
        generated = create_api_key()
        assert generated.raw.startswith(API_KEY_PREFIX)
        assert generated.token_hash == hash_api_key(generated.raw)
        assert generated.token_hash != generated.raw
        assert generated.raw.endswith(generated.last_four)
        assert generated.prefix == generated.raw[: len(generated.prefix)]

    def test_effective_grants_never_exceed_the_creator(self) -> None:
        stored = {"leads.view": "all", "deals.view": "all"}
        creator = {"leads.view": Scope.OWN}  # narrower, and no deals.view at all
        effective = _effective_grants(stored, creator)
        # Capped to the creator's scope, and the ungranted permission is dropped.
        assert effective == {"leads.view": Scope.OWN}

    def test_get_api_key_reads_header_and_bearer(self) -> None:
        raw = create_api_key().raw
        assert get_api_key(x_api_key=raw) == raw
        assert get_api_key(authorization=f"Bearer {raw}") == raw
        with pytest.raises(AuthenticationError):
            get_api_key(authorization="Bearer not-an-api-key")
        with pytest.raises(AuthenticationError):
            get_api_key()


# ----------------------------------------------------------- management


class TestManagement:
    async def test_create_stores_a_hash_and_returns_the_secret_once(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "keys@vantage.example")
        auth = _auth(organization, user.id)
        service = ApiKeyService(db, _settings())

        key, secret = await service.create(
            auth, user, name="CI", scopes={"leads.view": "all"}, expires_in_days=30
        )
        await db.flush()

        assert secret.startswith(API_KEY_PREFIX)
        row = (
            await db.execute(select(ApiKey).where(ApiKey.id == key.id))
        ).scalar_one()
        # The raw secret is nowhere in the row; only its hash and the display bits.
        assert row.token_hash == hash_api_key(secret)
        assert secret not in (row.token_hash, row.prefix, row.last_four)
        assert row.scopes == {"leads.view": "all"}
        assert row.expires_at is not None

    async def test_create_cannot_exceed_the_creators_authority(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "narrow@vantage.example")
        # Holds settings.manage, but leads.view only at OWN and no deals.view.
        auth = _auth(organization, user.id, **{"leads.view": Scope.OWN})
        auth.grants.pop("leads.manage", None)
        service = ApiKeyService(db, _settings())

        # Wider scope than held.
        with pytest.raises(PermissionDeniedError):
            await service.create(
                auth, user, name="x", scopes={"leads.view": "all"}, expires_in_days=None
            )
        # A permission the creator does not hold at all.
        with pytest.raises(PermissionDeniedError):
            await service.create(
                auth, user, name="x", scopes={"deals.view": "own"}, expires_in_days=None
            )

    async def test_create_requires_settings_manage(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "nomanage@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"leads.view": Scope.OWN},  # no settings.manage
        )
        with pytest.raises(PermissionDeniedError):
            await ApiKeyService(db, _settings()).create(
                auth, user, name="x", scopes={"leads.view": "own"}, expires_in_days=None
            )

    async def test_create_writes_an_audit_entry(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "audit@vantage.example")
        auth = _auth(organization, user.id)
        key, _ = await ApiKeyService(db, _settings()).create(
            auth, user, name="Audited", scopes={"leads.view": "all"}, expires_in_days=1
        )
        await db.flush()

        entry = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "api_key.created")
            )
        ).scalars().one()
        assert entry.entity_id == key.id
        # The secret is never in the audit metadata.
        assert "secret" not in (entry.metadata_ or {})

    async def test_rotate_changes_the_secret(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "rotate@vantage.example")
        auth = _auth(organization, user.id)
        service = ApiKeyService(db, _settings())
        key, first = await service.create(
            auth, user, name="r", scopes={"leads.view": "all"}, expires_in_days=30
        )
        await db.flush()

        rotated, second = await service.rotate(auth, user, key.id)
        assert first != second
        assert rotated.token_hash == hash_api_key(second)

    async def test_revoke_is_idempotent(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "revoke@vantage.example")
        auth = _auth(organization, user.id)
        service = ApiKeyService(db, _settings())
        key, _ = await service.create(
            auth, user, name="d", scopes={"leads.view": "all"}, expires_in_days=30
        )
        await db.flush()

        await service.revoke(auth, user, key.id)
        again = await service.revoke(auth, user, key.id)  # no error
        assert again.revoked_at is not None

    async def test_unknown_key_is_not_found(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        from uuid import uuid4

        user = await make_user(db, organization, "missing@vantage.example")
        auth = _auth(organization, user.id)
        with pytest.raises(NotFoundError):
            await ApiKeyService(db, _settings()).revoke(auth, user, uuid4())


# -------------------------------------------------------- authentication


class TestAuthentication:
    async def test_authenticate_yields_a_machine_principal(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ApiKeyService(db, _settings())
        key, secret = await service.create(
            auth, user, name="svc", scopes={"leads.view": "all"}, expires_in_days=30
        )
        await db.flush()

        context = await service.authenticate(secret)
        assert context.is_machine
        assert context.api_key_id == key.id
        assert context.user_id == user.id  # creator anchors scope + audit
        assert context.grants.get("leads.view") == Scope.ALL

    async def test_authenticate_rejects_an_unknown_key(
        self, db: AsyncSession
    ) -> None:
        with pytest.raises(AuthenticationError):
            await ApiKeyService(db, _settings()).authenticate("vk_not_a_real_key")

    async def test_authenticate_rejects_a_revoked_key(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ApiKeyService(db, _settings())
        key, secret = await service.create(
            auth, user, name="rev", scopes={"leads.view": "all"}, expires_in_days=30
        )
        await service.revoke(auth, user, key.id)
        await db.flush()
        with pytest.raises(AuthenticationError):
            await service.authenticate(secret)

    async def test_authenticate_rejects_an_expired_key(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ApiKeyService(db, _settings())
        key, secret = await service.create(
            auth, user, name="exp", scopes={"leads.view": "all"}, expires_in_days=1
        )
        key.expires_at = datetime.now(UTC) - timedelta(days=1)
        await db.flush()
        with pytest.raises(AuthenticationError):
            await service.authenticate(secret)

    async def test_rotated_secret_replaces_the_old_one(
        self, db: AsyncSession, admin
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ApiKeyService(db, _settings())
        key, first = await service.create(
            auth, user, name="rot", scopes={"leads.view": "all"}, expires_in_days=30
        )
        await db.flush()
        _, second = await service.rotate(auth, user, key.id)
        await db.flush()

        with pytest.raises(AuthenticationError):
            await service.authenticate(first)
        context = await service.authenticate(second)
        assert context.api_key_id == key.id


# ---------------------------------------------------- tenant isolation


class TestTenantIsolation:
    async def test_a_key_is_invisible_to_another_workspace(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        mine = await make_user(db, organization, "mine@vantage.example")
        my_auth = _auth(organization, mine.id)
        service = ApiKeyService(db, _settings())
        key, _ = await service.create(
            my_auth, mine, name="ours", scopes={"leads.view": "all"}, expires_in_days=30
        )
        await db.flush()

        theirs = await make_user(db, other_organization, "theirs@meridian.example")
        their_auth = _auth(other_organization, theirs.id)
        # Not in the other org's listing, and not loadable by id there.
        assert await service.list_keys(their_auth) == []
        with pytest.raises(NotFoundError):
            await service.revoke(their_auth, theirs, key.id)
