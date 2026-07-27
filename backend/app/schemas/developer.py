"""Developer portal contracts.

View models the portal renders: the API's coordinates, what the tenant's plan
entitles it to, the onboarding path, and the SDK catalogue. Composed by
`DeveloperService` from the existing entitlement and platform surfaces — this
module only names the shapes.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.api_key import ApiKeyRead


class ApiInfo(BaseModel):
    version: str
    base_url: str
    openapi_url: str
    docs_url: str


class EntitlementSummary(BaseModel):
    plan: str | None
    status: str
    #: Whether the effective plan grants programmatic API access and webhooks.
    api_access: bool
    webhooks: bool


class OnboardingStepRead(BaseModel):
    title: str
    body: str


class SdkTargetRead(BaseModel):
    language: str
    package_name: str
    description: str
    version: str


class DeveloperOverview(BaseModel):
    api: ApiInfo
    entitlements: EntitlementSummary
    onboarding: list[OnboardingStepRead]
    sdks: list[SdkTargetRead]
    quickstart_languages: list[str]


class QuickstartRead(BaseModel):
    base_url: str
    #: `{language: snippet}` — runnable first-call examples.
    examples: dict[str, str]


class SdkPackageRead(BaseModel):
    language: str
    version: str
    package_name: str
    #: `{relative_path: file_contents}` — the complete generated package.
    files: dict[str, str]


class ApiKeyGroups(BaseModel):
    """A workspace's keys split by environment, for the portal's two panels."""

    live: list[ApiKeyRead]
    sandbox: list[ApiKeyRead]


__all__ = [
    "ApiInfo",
    "ApiKeyGroups",
    "DeveloperOverview",
    "EntitlementSummary",
    "OnboardingStepRead",
    "QuickstartRead",
    "SdkPackageRead",
    "SdkTargetRead",
]
