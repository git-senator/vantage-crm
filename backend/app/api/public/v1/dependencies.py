"""Dependencies for the public API.

The public surface authenticates a *machine*, not a person: no cookie, no CSRF,
no JWT. Authentication reuses the Phase 7.1 machine-principal chain wholesale —
`get_machine_principal` resolves the key's tenant, binds RLS on a fresh
transaction, and yields a machine `AuthorizationContext`. Nothing here bypasses
it.

What this module adds on top:

  * `require_scope(...)` — the machine equivalent of the internal `require(...)`,
    asserting the key holds a permission before a handler runs. Scope is already
    bounded to the key's creator at authentication, so this is an ordinary RBAC
    check, not a second gate.
  * `actor_for(...)` — loads the creator `User` a write needs as its audit actor
    and ownership default. The services are shared with the internal API and
    take a `User`; a key acts *as* the person who minted it, which is exactly
    what makes its writes attributable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.api.v1.dependencies import MachineAuth, MachinePrincipal
from app.core.exceptions import AuthenticationError
from app.models.user import User
from app.repositories.user import UserRepository

__all__ = ["MachineAuth", "MachinePrincipal", "actor_for", "require_scope"]


def require_scope(
    *permissions: str,
) -> Callable[[MachinePrincipal], Awaitable[MachinePrincipal]]:
    """Assert the API key holds every named permission, then hand back the
    principal.

    Denial raises 403, rendered as a problem document by the shared handlers.
    Permissions are ANDed, matching the internal `require`.
    """

    async def _dependency(principal: MachineAuth) -> MachinePrincipal:
        for permission in permissions:
            principal.auth.require(permission)
        return principal

    return _dependency


async def actor_for(principal: MachinePrincipal) -> User:
    """Load the key's creator as the actor for a write.

    Authentication already refused a key whose creator is gone or inactive, so
    this normally succeeds; a `None` here means the account was disabled inside
    the same request and the write must not proceed.
    """
    if principal.auth.user_id is None:  # pragma: no cover - authenticate guards this
        raise AuthenticationError("API key is not valid.")
    user = await UserRepository(principal.session).get(
        principal.auth.user_id, principal.auth.organization_id
    )
    if user is None:
        raise AuthenticationError("API key is not valid.")
    return user
