"""The provider registry — the one place the set of integrations is assembled.

Dependency injection is deliberate: the registry is *built* from settings, and
`IntegrationService` accepts one rather than importing providers directly. That
keeps provider construction (which reads credentials) out of the service, lets a
test inject a deterministic fake, and makes "which integrations exist" a single
reviewable list rather than a scatter of imports.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.integrations.framework.provider import IntegrationProvider


class ProviderRegistry:
    """An immutable lookup of provider key -> provider."""

    def __init__(self, providers: dict[str, IntegrationProvider]) -> None:
        self._providers = dict(providers)

    def get(self, key: str) -> IntegrationProvider:
        provider = self._providers.get(key)
        if provider is None:
            raise NotFoundError(f"Unknown integration provider '{key}'.")
        return provider

    def has(self, key: str) -> bool:
        return key in self._providers

    def all(self) -> list[IntegrationProvider]:
        return [self._providers[key] for key in sorted(self._providers)]


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    """Assemble the providers this deployment offers.

    Not cached — `Settings` is unhashable and construction is cheap — and built
    per call for the same reason the billing provider factory is. Imports are
    local so the framework package does not drag every vendor module in at
    import time.
    """
    from app.integrations.providers.google.calendar import GoogleCalendarProvider
    from app.integrations.providers.google.contacts import GoogleContactsProvider

    providers: dict[str, IntegrationProvider] = {}
    for provider in (
        GoogleCalendarProvider(settings),
        GoogleContactsProvider(settings),
    ):
        providers[provider.metadata.key] = provider

    # A deterministic, no-network provider for local development and tests. Off
    # by default and refused in production (see assert_production_ready), because
    # a mock integration in production is a connection that pretends to sync.
    if settings.INTEGRATIONS_ENABLE_MOCK:
        from app.integrations.providers.mock import MockProvider

        mock = MockProvider()
        providers[mock.metadata.key] = mock

    return ProviderRegistry(providers)


__all__ = ["ProviderRegistry", "build_provider_registry"]
