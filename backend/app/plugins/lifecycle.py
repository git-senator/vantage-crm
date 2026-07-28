"""The plugin installation lifecycle — a small, explicit state machine.

An installation moves through three states: ``installed`` (present but inert),
``enabled`` (active — its subscriptions fire and its capabilities are live), and
``disabled`` (paused). Uninstalling removes the row entirely and is not a state.

The transitions are declared once, here, as pure data. A service asks
``next_state`` what an action produces and gets either the new state or a
``LifecycleError`` — so an illegal move (enabling something already enabled,
disabling something never enabled) is a precise refusal rather than a silent
no-op or an inconsistent row.
"""

from __future__ import annotations

from app.core.exceptions import AppError

#: The persisted states. `uninstalled` is deletion, not a state.
PLUGIN_STATES: tuple[str, ...] = ("installed", "enabled", "disabled")

#: The lifecycle actions and the (from -> to) transitions each permits.
_TRANSITIONS: dict[str, dict[str, str]] = {
    "enable": {"installed": "enabled", "disabled": "enabled"},
    "disable": {"enabled": "disabled"},
}


class LifecycleError(AppError):
    """An illegal lifecycle transition was requested."""


def next_state(current: str, action: str) -> str:
    """The state ``action`` produces from ``current``, or raise.

    Idempotence is *not* silently swallowed: enabling an already-enabled
    installation is an error, because the caller believes it is changing
    something and the honest answer is that there is nothing to change.
    """
    moves = _TRANSITIONS.get(action)
    if moves is None:
        raise LifecycleError(f"Unknown lifecycle action '{action}'.")
    target = moves.get(current)
    if target is None:
        raise LifecycleError(
            f"Cannot {action} a plugin that is '{current}'."
        )
    return target


def is_active(state: str) -> bool:
    return state == "enabled"


__all__ = [
    "PLUGIN_STATES",
    "LifecycleError",
    "is_active",
    "next_state",
]
