"""The plugin context — everything a handler is given, and nothing it is not.

A handler is called with a :class:`PluginContext`: the tenant and installation it
runs for, its granted capabilities, its non-secret configuration, and the
injected service clients it is allowed to use. It is deliberately a *narrow*
object — it exposes no database session, no ORM model, no internal service — so a
plugin cannot reach past the contract into the core. Capability checks are on the
context, so a handler asks ``context.require("records.write")`` before acting and
gets a clean :class:`SdkPermissionError` if it was never granted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.sdk.errors import SdkPermissionError

if TYPE_CHECKING:  # avoids a cycle: interfaces imports this module.
    from app.sdk.interfaces import AiClient, MessagingClient, RecordsClient


@dataclass(frozen=True, slots=True)
class PluginServices:
    """The injected service clients. A client is ``None`` when the plugin was not
    granted the capability that unlocks it, so the presence of the handle is
    itself the grant."""

    records: RecordsClient | None = None
    messaging: MessagingClient | None = None
    ai: AiClient | None = None


@dataclass(frozen=True, slots=True)
class PluginContext:
    organization_id: str
    installation_id: str
    plugin_key: str
    sdk_version: str
    capabilities: frozenset[str]
    config: Mapping[str, Any] = field(default_factory=dict)
    services: PluginServices = field(default_factory=PluginServices)

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    def require(self, capability: str) -> None:
        """Assert a capability, raising :class:`SdkPermissionError` if absent."""
        if capability not in self.capabilities:
            raise SdkPermissionError(
                f"Plugin '{self.plugin_key}' lacks capability '{capability}'."
            )


__all__ = ["PluginContext", "PluginServices"]
