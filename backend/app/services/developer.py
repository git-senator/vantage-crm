"""The developer platform service.

Aggregation glue, not new behaviour. It composes the developer portal's views
from surfaces the earlier phases already own:

  * entitlements and plan status from `EntitlementService` (Phase 7.5),
  * the tenant's keys from `ApiKeyService` (Phase 7.1), grouped by environment,
  * the OpenAPI document from `app.developer.spec` (the public API's own),
  * SDK packages from `app.developer.sdk`, and onboarding copy from
    `app.developer.onboarding`.

It never reaches past those services into a repository, and it adds no new
persistence. The one gate it applies is `settings.manage` on the key listing —
delegated to `ApiKeyService`, which already enforces it — because keys are an
administrative surface; the docs, spec, quickstarts and SDKs are readable by any
authenticated member, since they describe the public contract, not tenant data.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.developer.onboarding import onboarding_steps, quickstarts
from app.developer.sdk import (
    DEFAULT_BASE_URL,
    SDK_TARGETS,
    SdkPackage,
    generate_sdk,
)
from app.developer.spec import public_api_version, public_openapi_spec
from app.schemas.api_key import to_read as api_key_to_read
from app.schemas.developer import (
    ApiInfo,
    ApiKeyGroups,
    DeveloperOverview,
    EntitlementSummary,
    OnboardingStepRead,
    QuickstartRead,
    SdkPackageRead,
    SdkTargetRead,
)
from app.services.api_key import ApiKeyService
from app.services.billing.catalog import FEATURE_API_ACCESS, FEATURE_WEBHOOKS
from app.services.billing.service import EntitlementService
from app.services.rbac import AuthorizationContext


class DeveloperService:
    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings

    async def overview(self, *, base_url: str = DEFAULT_BASE_URL) -> DeveloperOverview:
        entitlements = EntitlementService(self.session, self.auth)
        summary = await entitlements.summary()
        features = summary.get("features", {})
        return DeveloperOverview(
            api=self._api_info(base_url),
            entitlements=EntitlementSummary(
                plan=summary.get("plan_key"),
                status=str(summary.get("status", "none")),
                api_access=bool(features.get(FEATURE_API_ACCESS)),
                webhooks=bool(features.get(FEATURE_WEBHOOKS)),
            ),
            onboarding=[
                OnboardingStepRead(title=step.title, body=step.body)
                for step in onboarding_steps(base_url)
            ],
            sdks=self._sdk_targets(),
            quickstart_languages=sorted(quickstarts(base_url)),
        )

    def quickstart(self, *, base_url: str = DEFAULT_BASE_URL) -> QuickstartRead:
        return QuickstartRead(base_url=base_url, examples=quickstarts(base_url))

    def sdk_targets(self) -> list[SdkTargetRead]:
        return self._sdk_targets()

    def generate_sdk(
        self, language: str, *, base_url: str = DEFAULT_BASE_URL
    ) -> SdkPackageRead:
        """Render an SDK package for `language`, or 404 for an unknown one."""
        try:
            package: SdkPackage = generate_sdk(
                language,
                public_openapi_spec(),
                version=public_api_version(),
                base_url=base_url,
            )
        except ValueError as exc:
            raise NotFoundError(str(exc)) from exc
        return SdkPackageRead(
            language=package.language,
            version=package.version,
            package_name=package.package_name,
            files=package.files,
        )

    def openapi(self) -> dict[str, Any]:
        """The public API's OpenAPI document, verbatim."""
        return public_openapi_spec()

    async def api_keys(self) -> ApiKeyGroups:
        """The workspace's keys, split live/sandbox. Gated on `settings.manage`
        inside `ApiKeyService.list_keys`."""
        keys = await ApiKeyService(self.session, self.settings).list_keys(self.auth)
        live = [api_key_to_read(k) for k in keys if k.environment != "sandbox"]
        sandbox = [api_key_to_read(k) for k in keys if k.environment == "sandbox"]
        return ApiKeyGroups(live=live, sandbox=sandbox)

    # ----------------------------------------------------------- helpers

    def _api_info(self, base_url: str) -> ApiInfo:
        return ApiInfo(
            version=public_api_version(),
            base_url=base_url,
            openapi_url=f"{base_url}/openapi.json",
            docs_url=f"{base_url}/docs",
        )

    def _sdk_targets(self) -> list[SdkTargetRead]:
        version = public_api_version()
        return [
            SdkTargetRead(
                language=target.language,
                package_name=target.package_name,
                description=target.description,
                version=version,
            )
            for target in SDK_TARGETS
        ]


__all__ = ["DeveloperService"]
