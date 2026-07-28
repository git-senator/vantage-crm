"""Backend extension hooks — the named points a runtime dispatches through.

A hook is an extension point with a stable name and a documented payload. The
platform's runtime invokes a hook; registered extensions attached to it run. The
catalogue here is the contract (what hooks exist and what each carries); the
:class:`HookRegistry` is the in-process wiring a runtime uses to attach and find
handlers.

This is the SDK's *description* of the extension surface — pure data and a small
registry, with no dependency on the live runtime, so a developer can see and
target the hook points without running the CRM. The existing plugin runtime is
not touched; a future dispatcher consults a registry like this.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

#: A hook handler: given a context-and-payload mapping, does its work.
HookHandler = Callable[..., Awaitable[None]]


@dataclass(frozen=True, slots=True)
class HookPoint:
    name: str
    description: str
    #: The event type carried, when the hook fans a CRM event; else None.
    event_type: str | None = None


HOOK_POINTS: dict[str, HookPoint] = {
    h.name: h
    for h in (
        HookPoint("event.received",
                  "A subscribed CRM event was delivered to the plugin."),
        HookPoint("install.configured",
                  "The plugin's configuration was created or changed."),
        HookPoint("install.enabled", "The installation was enabled."),
        HookPoint("install.disabled", "The installation was disabled."),
    )
}


def hook_point(name: str) -> HookPoint:
    try:
        return HOOK_POINTS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown hook '{name}'.") from exc


class HookRegistry:
    """An in-process registry of handlers per hook point.

    Deterministic and dependency-free: a runtime constructs one, extensions
    register into it, and the dispatcher reads it. Registering against an unknown
    hook is a `KeyError`, so a typo in a hook name fails at wiring time.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = {
            name: [] for name in HOOK_POINTS
        }

    def register(self, hook_name: str, handler: HookHandler) -> None:
        hook_point(hook_name)  # validate
        self._handlers[hook_name].append(handler)

    def handlers(self, hook_name: str) -> list[HookHandler]:
        return list(self._handlers.get(hook_name, ()))

    def hooks(self) -> list[str]:
        return [name for name, handlers in self._handlers.items() if handlers]


__all__ = [
    "HOOK_POINTS",
    "HookHandler",
    "HookPoint",
    "HookRegistry",
    "hook_point",
]
