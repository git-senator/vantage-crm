"""The sidebar's attention counts.

One endpoint rather than four, because the sidebar needs all of them together
and asks again on a timer: four round trips per poll to move one dot is three
too many.

No `require(...)` on the route itself — every count gates on its own permission
inside the service, and a caller who can see none of them gets four zeros
rather than a 403. A signed-in person always has a sidebar; it may simply have
nothing lit on it.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.dependencies import Authorization, CurrentUser, TenantSessionDep
from app.schemas.attention import AttentionRead
from app.services.attention import AttentionService

router = APIRouter()


@router.get("", response_model=AttentionRead)
async def attention_summary(
    session: TenantSessionDep,
    auth: Authorization,
    _user: CurrentUser,
) -> AttentionRead:
    """What is waiting on the caller, in four numbers."""
    return await AttentionService(session, auth).summary()
