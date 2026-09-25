"""Developer platform (Phase 7.6): sandbox keys, the SDK generator, the portal.

The properties that carry the milestone:

  * **Sandbox keys are real credentials but free.** A `sandbox` key authenticates
    exactly like a live one, carries its environment on the machine principal,
    and is exempt from the plan gate and the API-key quota.
  * **SDKs are generated from the public API's own OpenAPI document**, are
    versioned to it, are deterministic, and — for Python — actually compile.
  * **The portal reuses the existing services**: entitlements, keys, and the
    generator, composed into the developer views without new persistence.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.developer.onboarding import onboarding_steps, quickstarts
from app.developer.sdk import (
    SDK_TARGETS,
    extract_operations,
    generate_sdk,
)
from app.developer.spec import public_api_version, public_openapi_spec
from app.models.organization import Organization
from app.schemas.api_key import ApiKeyCreate
from app.services.api_key import ApiKeyService
from app.services.developer import DeveloperService
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


def _auth(organization: Organization, user_id, **grants: Scope) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    resolved = {
        "settings.manage": Scope.ALL,
        "leads.view": Scope.ALL,
        **grants,
    }
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants=resolved,
    )


# ------------------------------------------------------- the SDK generator
# These are pure and need no database.


class TestSpec:
    def test_public_spec_is_openapi_with_a_version(self) -> None:
        spec = public_openapi_spec()
        assert spec.get("openapi", "").startswith("3.")
        assert public_api_version() == spec["info"]["version"]
        assert "/leads" in spec["paths"]

    def test_operations_are_extracted_and_sorted(self) -> None:
        operations = extract_operations(public_openapi_spec())
        assert operations
        # Sorted and stable.
        keys = [(op.resource, op.path, op.http_method) for op in operations]
        assert keys == sorted(keys)
        # The leads collection yields list + create.
        leads = {op.name for op in operations if op.resource == "leads"}
        assert {"list", "create"} <= leads


class TestGeneratedSdks:
    def test_typescript_package_is_complete_and_versioned(self) -> None:
        version = public_api_version()
        package = generate_sdk(
            "typescript", public_openapi_spec(), version=version
        )
        assert package.language == "typescript"
        assert package.version == version
        assert set(package.files) == {
            "package.json",
            "README.md",
            "src/index.ts",
            "src/client.ts",
        }
        client = package.files["src/client.ts"]
        assert "export class RossaClient" in client
        assert "LeadsResource" in client
        assert version in client
        assert version in package.files["package.json"]

    def test_python_package_compiles(self) -> None:
        version = public_api_version()
        package = generate_sdk("python", public_openapi_spec(), version=version)
        assert set(package.files) == {
            "pyproject.toml",
            "README.md",
            "rossa_crm/__init__.py",
            "rossa_crm/client.py",
        }
        client = package.files["rossa_crm/client.py"]
        assert "class RossaClient" in client
        assert "class LeadsResource(_Resource)" in client
        # The generated client must be valid Python, not just plausible text.
        compile(client, "rossa_crm/client.py", "exec")
        compile(package.files["rossa_crm/__init__.py"], "__init__.py", "exec")

    def test_generation_is_deterministic(self) -> None:
        spec = public_openapi_spec()
        first = generate_sdk("python", spec, version="1.2.3").files
        second = generate_sdk("python", spec, version="1.2.3").files
        assert first == second

    def test_base_url_flows_into_the_client(self) -> None:
        package = generate_sdk(
            "typescript",
            public_openapi_spec(),
            version="1.0.0",
            base_url="https://crm.acme.test/api/public/v1",
        )
        assert "https://crm.acme.test/api/public/v1" in package.files["src/client.ts"]

    def test_unknown_language_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            generate_sdk("cobol", public_openapi_spec(), version="1.0.0")

    def test_targets_cover_typescript_and_python(self) -> None:
        languages = {t.language for t in SDK_TARGETS}
        assert languages == {"typescript", "python"}


class TestOnboarding:
    def test_steps_and_quickstarts_use_the_base_url(self) -> None:
        base = "https://crm.acme.test/api/public/v1"
        steps = onboarding_steps(base)
        assert steps and any(base in s.body for s in steps)
        examples = quickstarts(base)
        assert set(examples) == {"curl", "typescript", "python"}
        assert base in examples["curl"]


# ------------------------------------------------------------ sandbox keys


class TestSandboxKeys:
    async def test_schema_defaults_to_live_and_accepts_sandbox(self) -> None:
        assert ApiKeyCreate(name="x", scopes={"leads.view": "all"}).environment == "live"
        sandbox = ApiKeyCreate(
            name="x", scopes={"leads.view": "all"}, environment="sandbox"
        )
        assert sandbox.environment == "sandbox"

    async def test_sandbox_key_carries_its_environment(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "sandbox@rossa.example")
        auth = _auth(organization, user.id)
        service = ApiKeyService(db, _settings())

        key, secret = await service.create(
            auth,
            user,
            name="test key",
            scopes={"leads.view": "all"},
            expires_in_days=30,
            environment="sandbox",
        )
        await db.flush()
        assert key.environment == "sandbox"
        assert key.is_sandbox

        context = await service.authenticate(secret)
        assert context.api_key_environment == "sandbox"
        assert context.is_sandbox

    async def test_unknown_environment_is_rejected(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        from app.core.exceptions import AppError

        user = await make_user(db, organization, "bogus-env@rossa.example")
        auth = _auth(organization, user.id)
        with pytest.raises(AppError):
            await ApiKeyService(db, _settings()).create(
                auth,
                user,
                name="x",
                scopes={"leads.view": "all"},
                expires_in_days=None,
                environment="staging",
            )

    async def test_sandbox_skips_the_billing_gate_that_blocks_live(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """With enforcement on and no plan, a live key is refused (no api_access)
        but a sandbox key is minted — it is exempt from the gate and the quota."""
        user = await make_user(db, organization, "gate@rossa.example")
        auth = _auth(organization, user.id)
        service = ApiKeyService(db, _settings(BILLING_ENFORCED=True))

        with pytest.raises(PermissionDeniedError):
            await service.create(
                auth,
                user,
                name="live",
                scopes={"leads.view": "all"},
                expires_in_days=None,
                environment="live",
            )

        key, _ = await service.create(
            auth,
            user,
            name="sandbox",
            scopes={"leads.view": "all"},
            expires_in_days=None,
            environment="sandbox",
        )
        assert key.is_sandbox


# -------------------------------------------------------- developer service


class TestDeveloperService:
    async def test_overview_reports_api_and_sdk_targets(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "dev@rossa.example")
        auth = _auth(organization, user.id)
        overview = await DeveloperService(db, auth, _settings()).overview(
            base_url="https://crm.acme.test/api/public/v1"
        )
        assert overview.api.version == public_api_version()
        assert overview.api.openapi_url.endswith("/openapi.json")
        assert {t.language for t in overview.sdks} == {"typescript", "python"}
        assert overview.onboarding
        assert set(overview.quickstart_languages) == {"curl", "python", "typescript"}

    async def test_generate_sdk_unknown_language_is_not_found(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "haskell@rossa.example")
        auth = _auth(organization, user.id)
        service = DeveloperService(db, auth, _settings())
        assert service.generate_sdk("typescript").language == "typescript"
        with pytest.raises(NotFoundError):
            service.generate_sdk("haskell")

    async def test_api_keys_are_grouped_by_environment(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "grouped@rossa.example")
        auth = _auth(organization, user.id)
        keys = ApiKeyService(db, _settings())
        await keys.create(
            auth, user, name="live one", scopes={"leads.view": "all"},
            expires_in_days=30, environment="live",
        )
        await keys.create(
            auth, user, name="sandbox one", scopes={"leads.view": "all"},
            expires_in_days=30, environment="sandbox",
        )
        await db.flush()

        groups = await DeveloperService(db, auth, _settings()).api_keys()
        assert [k.environment for k in groups.live] == ["live"]
        assert [k.environment for k in groups.sandbox] == ["sandbox"]
        assert groups.live[0].environment == "live"
