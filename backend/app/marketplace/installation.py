"""The integration installation lifecycle — the operational tracking states.

Distinct from the plugin lifecycle (installed/enabled/disabled, which is the
*runtime* state) and the listing publication lifecycle, this is the state of a
tenant's *operational record* of an integration: it was installed, it is active,
it is mid-upgrade, or it was uninstalled and now survives only as history.

    pending -> active -> upgrading -> active
                  \\-> uninstalled       /
    pending -> failed

Keeping this as its own small machine means the install-history table records a
true operational story — an org that installed, upgraded, and later removed an
integration leaves a legible trail — without overloading the plugin runtime's
three states. Pure and dependency-free.
"""

from __future__ import annotations

#: The persisted operational states of an installation record.
INSTALLATION_STATES: tuple[str, ...] = (
    "pending",
    "active",
    "upgrading",
    "uninstalled",
    "failed",
)

_TRANSITIONS: dict[str, dict[str, str]] = {
    "activate": {"pending": "active"},
    "begin_upgrade": {"active": "upgrading"},
    "complete_upgrade": {"upgrading": "active"},
    "cancel_upgrade": {"upgrading": "active"},
    "uninstall": {
        "pending": "uninstalled",
        "active": "uninstalled",
        "upgrading": "uninstalled",
    },
    "fail": {"pending": "failed", "upgrading": "active"},
}


class InstallationStateError(Exception):
    """An illegal installation-record transition was requested."""


def next_state(current: str, action: str) -> str:
    """The state ``action`` produces from ``current``, or raise."""
    moves = _TRANSITIONS.get(action)
    if moves is None:
        raise InstallationStateError(f"Unknown installation action '{action}'.")
    target = moves.get(current)
    if target is None:
        raise InstallationStateError(
            f"Cannot {action} an installation that is '{current}'."
        )
    return target


def is_active(state: str) -> bool:
    """Whether the record represents a live installation (active or upgrading)."""
    return state in ("active", "upgrading")


def is_history(state: str) -> bool:
    """Whether the record is a closed chapter — uninstalled or failed."""
    return state in ("uninstalled", "failed")


__all__ = [
    "INSTALLATION_STATES",
    "InstallationStateError",
    "is_active",
    "is_history",
    "next_state",
]
