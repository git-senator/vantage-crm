"""What a running job gets: a session, a tenant, and an authorization context.

The awkward part of running business logic outside a request is that services
take an `AuthorizationContext` — they are written to ask "what may *this actor*
do", and there is no actor behind a cron tick. Two ways to resolve that, and
only one of them is safe:

  * fork the service and let the job path skip the checks — which quietly
    creates a second, laxer implementation of every rule; or
  * give the job a real, explicit, maximally-scoped context and keep one
    implementation.

This module does the second. `system_context` is honest about what it is: a
context with ALL scope on the permissions the job needs, tagged with the
`system` role so an audit entry written under it is identifiable as machine
work rather than something a person did. It is granted per job, not globally —
the scan job gets `documents.*` and nothing else.

`tenant_scope` binds RLS the same way the request dependency does, with
`SET LOCAL`, so a pooled connection cannot carry one tenant's scope into the
next job.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Scope
from app.db.session import session_scope
from app.services.rbac import AuthorizationContext

#: A stand-in actor id for machine-initiated work. Not a real user, and no user
#: row has it — audit entries written by a job carry a NULL actor and this role
#: key, which is what distinguishes "the scanner quarantined this" from "an
#: administrator quarantined this".
SYSTEM_ROLE_KEY = "system"


def system_context(organization_id: UUID, *permissions: str) -> AuthorizationContext:
    """An authorization context for machine work in one tenant.

    Deliberately explicit about which permissions it carries. A job that only
    needs to read documents should not be handed a context that could delete a
    deal, even though nothing in that job would try to — the point is that the
    grant is visible at the call site and reviewable.
    """
    return AuthorizationContext(
        user_id=None,
        organization_id=organization_id,
        role_keys=(SYSTEM_ROLE_KEY,),
        grants=dict.fromkeys(permissions, Scope.ALL),
    )


@asynccontextmanager
async def tenant_scope(organization_id: UUID) -> AsyncIterator[AsyncSession]:
    """A transaction with RLS bound to one tenant, for job work."""
    async with session_scope(organization_id=organization_id) as session:
        yield session


@asynccontextmanager
async def unscoped_scope() -> AsyncIterator[AsyncSession]:
    """A transaction with **no** tenant context.

    Only two things may legitimately use this: resolving the tenant list via
    the `SECURITY DEFINER` function, and writing an infrastructure-level
    `job_failures` row. Every business query needs `tenant_scope`, because
    without a bound tenant RLS returns nothing and the job would appear to
    succeed while doing nothing at all.
    """
    async with session_scope() as session:
        yield session


async def active_organization_ids(session: AsyncSession) -> list[UUID]:
    """Every tenant a sweep should visit.

    Reads through `list_active_organization_ids()`, which is `SECURITY DEFINER`
    and owned by the BYPASSRLS role — see `app/db/sql_objects.py` for why the
    worker gets a list of ids rather than a blanket RLS exemption.
    """
    rows = await session.execute(text("SELECT list_active_organization_ids()"))
    return [row[0] for row in rows.all()]
