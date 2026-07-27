"""Binding a correlation id (and tenant/actor) onto a unit of work.

HTTP requests already carry a correlation id — the middleware sets one and every
log line picks it up from a `contextvar`. This gives the *other* surfaces the
same property with one call: a background job, an AI operation triggered outside
a request, and a webhook delivery each open a correlation scope, so their logs
are as traceable as an HTTP request's, keyed by an id that ties an outbox event
to the deliveries it produced.

It reuses the logging context vars rather than inventing a parallel set, so the
JSON formatter needs no change — a job's lines simply gain a `request_id`,
`organization_id` and `user_id` the same way a request's do.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.logging import org_id_var, request_id_var, user_id_var


def new_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def correlation_scope(
    correlation_id: str | None = None,
    *,
    organization_id: object | None = None,
    user_id: object | None = None,
) -> Iterator[str]:
    """Bind a correlation id for the duration of a unit of work.

    A missing id is generated. Tenant and actor are bound when known, so a job's
    logs carry the same fields a request's do. All three vars are reset on exit,
    so a pooled worker task cannot leak one job's context into the next.
    """
    resolved = correlation_id or new_id()
    tokens = [request_id_var.set(resolved)]
    if organization_id is not None:
        tokens.append(org_id_var.set(str(organization_id)))
    if user_id is not None:
        tokens.append(user_id_var.set(str(user_id)))
    try:
        yield resolved
    finally:
        for token in reversed(tokens):
            token.var.reset(token)
