"""Webhook authentication.

A provider posting inbound mail has no session, no cookie and no CSRF token. It
authenticates with an HMAC-SHA256 of the **raw request body** under a shared
secret, sent in `X-Vantage-Signature`, plus a timestamp header that bounds
replay.

Four things this gets right that a naive version does not:

1. **The signature covers the raw bytes, not the parsed model.** Re-serialising
   JSON and signing that is a different string than the one the sender signed,
   and the difference shows up only for payloads with unusual key order or
   unicode — that is, in production, months later.
2. **Comparison is constant-time.** A byte-by-byte `==` on a MAC leaks its
   prefix to a patient attacker.
3. **A timestamp is signed and bounded.** Without it a captured request can be
   replayed indefinitely. The message-level deduplication behind this is a
   correctness measure, not a security one.
4. **The tenant comes from a header that is inside the signature.** It must not
   be possible to post a signed-for-one-tenant body at another tenant.

The secret is per-deployment rather than per-tenant, because there is one mail
provider and one endpoint. When multi-tenant activation brings per-org inbound
addresses, this becomes a lookup — which is why the tenant is already a
parameter rather than an assumption.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.db.session import get_session_factory, set_tenant_context

logger = get_logger(__name__)

SIGNATURE_HEADER = "X-Vantage-Signature"
TIMESTAMP_HEADER = "X-Vantage-Timestamp"
ORGANIZATION_HEADER = "X-Vantage-Organization"

#: How far out of step a webhook's clock may be. Five minutes is the usual
#: provider tolerance; wider turns replay protection into decoration.
MAX_SKEW_SECONDS = 300


def _expected_signature(secret: str, timestamp: str, organization: str, body: bytes) -> str:
    payload = b".".join(
        [timestamp.encode(), organization.encode(), body]
    )
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


async def verify_inbound_signature(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    x_vantage_signature: Annotated[str | None, Header()] = None,
    x_vantage_timestamp: Annotated[str | None, Header()] = None,
    x_vantage_organization: Annotated[str | None, Header()] = None,
) -> UUID:
    """Authenticate a webhook and return the tenant it is for."""
    secret = settings.INBOUND_WEBHOOK_SECRET.get_secret_value()
    if not secret:
        # Refusing is the only safe response. An unset secret must never mean
        # "accept everything", which is what a default-open check would do.
        logger.error("inbound_webhook_secret_unset")
        raise AuthenticationError("Inbound webhooks are not configured.")

    if not (x_vantage_signature and x_vantage_timestamp and x_vantage_organization):
        raise AuthenticationError("Missing webhook authentication headers.")

    try:
        sent_at = int(x_vantage_timestamp)
    except ValueError as exc:
        raise AuthenticationError("Invalid webhook timestamp.") from exc

    if abs(time.time() - sent_at) > MAX_SKEW_SECONDS:
        raise AuthenticationError("Webhook timestamp is outside the accepted window.")

    body = await request.body()
    expected = _expected_signature(
        secret, x_vantage_timestamp, x_vantage_organization, body
    )
    if not hmac.compare_digest(expected, x_vantage_signature):
        # No detail: a caller that cannot produce a signature learns nothing
        # about why. The log line is where the diagnosis lives.
        logger.warning(
            "inbound_webhook_signature_invalid",
            extra={"organization": x_vantage_organization},
        )
        raise AuthenticationError("Invalid webhook signature.")

    try:
        return UUID(x_vantage_organization)
    except ValueError as exc:
        raise AuthenticationError("Invalid organization header.") from exc


async def get_webhook_session(
    organization_id: Annotated[UUID, Depends(verify_inbound_signature)],
) -> AsyncIterator[tuple[AsyncSession, UUID]]:
    """A tenant-bound transaction for an authenticated webhook.

    Binds RLS exactly as a request does — a webhook is not an excuse to run
    unscoped. The tenant comes from the *signed* header, so a caller cannot
    point a validly-signed body at somebody else's data.
    """
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await set_tenant_context(session, organization_id)
        yield session, organization_id


InboundTenantDep = Annotated[
    tuple[AsyncSession, UUID], Depends(get_webhook_session)
]
