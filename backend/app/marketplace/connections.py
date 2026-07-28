"""The OAuth / auth-provider abstraction, and the marketplace connection state.

An integration authenticates in one of a few shapes, and a listing needs to
*declare* which without the marketplace having to know how to perform it. That
declaration is :class:`AuthSpec`: the method, the OAuth scopes it would request,
whether it needs a stored secret, and — crucially — an optional ``provider_key``
that links the listing to a live Phase 7.7 integration provider when one exists.

This is an abstraction, not an engine. The actual OAuth handshake, token sealing
and refresh already live in the Phase 7.7 integration runtime; the marketplace
does not restate any of it. A listing whose ``provider_key`` names a live provider
connects through that runtime; a listing without one is a template awaiting a
provider, and says so honestly rather than pretending to connect.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: How a listing authenticates.
#:   * ``oauth2``  — a consent handshake (delegated to the Phase 7.7 runtime).
#:   * ``api_key`` — a secret the tenant pastes, sealed at rest.
#:   * ``webhook`` — an inbound/outbound URL, no credential handshake.
#:   * ``none``    — needs no authentication at all.
AUTH_METHODS: tuple[str, ...] = ("oauth2", "api_key", "webhook", "none")

#: The marketplace-level connection state of an installed integration. Distinct
#: from the plugin lifecycle (installed/enabled/disabled): this answers "can it
#: actually reach the provider right now?", rolled up from the auth readiness and
#: any live connection.
CONNECTION_STATES: tuple[str, ...] = (
    "not_connected",  # installed but its credential/consent is not in place
    "connected",  # authenticated and ready
    "error",  # authenticated once, now failing
    "expired",  # a credential that needs renewing
)


def validate_auth_method(method: str) -> str:
    """Return ``method`` if known, else raise ``ValueError``."""
    if method not in AUTH_METHODS:
        raise ValueError(f"Unknown auth method '{method}'.")
    return method


def is_oauth(method: str) -> bool:
    return method == "oauth2"


@dataclass(frozen=True, slots=True)
class AuthSpec:
    """How a listing authenticates, as declarative data."""

    method: str
    #: OAuth scopes requested at consent, for an ``oauth2`` listing.
    scopes: tuple[str, ...] = ()
    #: A live Phase 7.7 provider this listing wraps, when one exists.
    provider_key: str | None = None
    #: Whether the listing stores a secret credential (an API key / webhook URL).
    requires_secret: bool = False

    def __post_init__(self) -> None:
        validate_auth_method(self.method)


def auth_spec(
    method: str,
    *,
    scopes: tuple[str, ...] = (),
    provider_key: str | None = None,
    requires_secret: bool = False,
) -> AuthSpec:
    """Build a validated :class:`AuthSpec`."""
    return AuthSpec(
        method=method,
        scopes=scopes,
        provider_key=provider_key,
        requires_secret=requires_secret,
    )


@dataclass(frozen=True, slots=True)
class ConnectionReadiness:
    """Whether an installed integration's auth is actually in place."""

    method: str
    #: The required secret config keys the listing declares.
    required_secret_keys: frozenset[str] = field(default_factory=frozenset)
    #: The secret keys actually set on the installation.
    present_secret_keys: frozenset[str] = field(default_factory=frozenset)
    #: For an ``oauth2`` listing, whether a live connection is active. ``None``
    #: when the listing does not go through a live provider.
    live_connection_active: bool | None = None

    @property
    def secrets_satisfied(self) -> bool:
        return self.required_secret_keys <= self.present_secret_keys

    @property
    def is_ready(self) -> bool:
        """Auth is in place: the declared secrets are set and, for an OAuth
        listing wrapping a live provider, that connection is active."""
        if not self.secrets_satisfied:
            return False
        if self.method == "oauth2" and self.live_connection_active is not None:
            return self.live_connection_active
        return True


def connection_state(readiness: ConnectionReadiness) -> str:
    """The marketplace connection state from a readiness snapshot."""
    if readiness.method == "none":
        return "connected"
    if (
        readiness.method == "oauth2"
        and readiness.live_connection_active is False
        and readiness.secrets_satisfied
    ):
        # Consent lapsed on a provider that had connected before.
        return "expired"
    return "connected" if readiness.is_ready else "not_connected"


__all__ = [
    "AUTH_METHODS",
    "CONNECTION_STATES",
    "AuthSpec",
    "ConnectionReadiness",
    "auth_spec",
    "connection_state",
    "is_oauth",
    "validate_auth_method",
]
