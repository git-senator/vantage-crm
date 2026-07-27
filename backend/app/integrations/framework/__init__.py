"""The provider-agnostic integration core."""

from __future__ import annotations

from app.integrations.framework.provider import IntegrationProvider
from app.integrations.framework.registry import (
    ProviderRegistry,
    build_provider_registry,
)
from app.integrations.framework.types import (
    INTEGRATION_EVENT_TYPES,
    OAuthTokens,
    ProviderMetadata,
    SyncOutcome,
)

__all__ = [
    "INTEGRATION_EVENT_TYPES",
    "IntegrationProvider",
    "OAuthTokens",
    "ProviderMetadata",
    "ProviderRegistry",
    "SyncOutcome",
    "build_provider_registry",
]
