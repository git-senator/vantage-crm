"""Extension and service-injection interfaces.

Two kinds of contract:

  * The interfaces a plugin **implements** — :class:`Extension` (its identity) and
    :class:`EventHandler` (a typed reaction to events).
  * The interfaces a plugin **calls** — :class:`RecordsClient`,
    :class:`MessagingClient` and :class:`AiClient`. These are the service-
    injection seam: a plugin declares that it needs, say, a ``RecordsClient``,
    and the runtime injects an implementation backed by the public REST API. The
    plugin depends on the *protocol*, never on a CRM service class, which is what
    keeps a plugin free of internal imports.

All ``Protocol``s, so a plugin satisfies them structurally — no base class to
import, no inheritance to get wrong.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from app.sdk.context import PluginContext
from app.sdk.events import PluginEvent


@runtime_checkable
class Extension(Protocol):
    """The identity every extension exposes."""

    @property
    def key(self) -> str: ...

    @property
    def version(self) -> str: ...


@runtime_checkable
class EventHandler(Protocol):
    """An extension that reacts to CRM events."""

    #: The event types this handler wants, e.g. ``("deal.created",)``.
    @property
    def events(self) -> tuple[str, ...]: ...

    async def handle(self, event: PluginEvent, context: PluginContext) -> None:
        """React to one event. Raising is reported as a plugin error; it never
        breaks the CRM transaction that emitted the event."""
        ...


# ------------------------------------------------- service-injection interfaces


@runtime_checkable
class RecordsClient(Protocol):
    """A capability-scoped view of the CRM records, backed by the public API."""

    async def get(self, entity: str, record_id: str) -> Mapping[str, Any]: ...

    async def create(
        self, entity: str, attributes: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    async def update(
        self, entity: str, record_id: str, attributes: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class MessagingClient(Protocol):
    async def send(
        self, contact_id: str, body: str, *, channel: str = "email"
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class AiClient(Protocol):
    async def complete(
        self, prompt: str, *, max_tokens: int = 512
    ) -> str: ...


__all__ = [
    "AiClient",
    "EventHandler",
    "Extension",
    "MessagingClient",
    "RecordsClient",
]
