"""WhatsApp webhook — Meta's shape, our ingestion pipeline.

Two endpoints on one path, which is Meta's design rather than ours:

* `GET` is the **subscription handshake**. Meta calls it once when the webhook
  is registered, echoing a challenge that must be returned as bare text. It
  authenticates nothing afterwards.
* `POST` is the **delivery**, signed with `X-Hub-Signature-256` — HMAC-SHA256 of
  the raw body under the app secret.

The differences from the mail webhook are all Meta's:

* the signature header is `sha256=<hex>`, prefixed;
* there is **no timestamp**, so replay is bounded by message-level
  deduplication rather than by a window. That is weaker, and it is why dedupe on
  the provider id is a correctness requirement here rather than a nicety;
* there is **no tenant header**, because Meta has no idea we are multi-tenant.
  The tenant is resolved from the business phone number the message was sent to.

Everything after parsing is the same `InboundMessageService` pipeline email
uses — that is the return on making `channel` a column in Phase 3.4.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.db.session import get_session_factory, set_tenant_context
from app.schemas.conversation import InboundResult
from app.services.conversation import InboundMessageService
from app.services.messaging import parse_inbound

logger = get_logger(__name__)

router = APIRouter()

SIGNATURE_HEADER = "X-Hub-Signature-256"


async def verify_meta_signature(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> bytes:
    """Authenticate a Meta webhook and return the raw body.

    Returns the bytes rather than re-reading them downstream: the signature
    covers exactly these bytes, and a second `await request.body()` that
    happened to differ would mean verifying one thing and parsing another.
    """
    secret = settings.WHATSAPP_APP_SECRET.get_secret_value()
    if not secret:
        # Same rule as inbound mail: an unset secret refuses everything. A
        # default-open check would accept anything that posted.
        logger.error("whatsapp_app_secret_unset")
        raise AuthenticationError("WhatsApp webhooks are not configured.")

    header = request.headers.get(SIGNATURE_HEADER)
    if not header or not header.startswith("sha256="):
        raise AuthenticationError("Missing webhook signature.")

    body = await request.body()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, header.removeprefix("sha256=")):
        logger.warning("whatsapp_signature_invalid")
        raise AuthenticationError("Invalid webhook signature.")

    return body


@router.get("/webhook", response_class=Response)
async def verify_subscription(
    settings: Annotated[Settings, Depends(get_settings)],
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
) -> Response:
    """Meta's one-time subscription handshake.

    Echoes the challenge as **bare text**, not JSON — Meta compares the body
    byte for byte and a JSON-quoted string fails the check.

    Constant-time comparison even though this token is not a signature: it is
    still a secret, and leaking its prefix through timing costs nothing to
    avoid.
    """
    expected = settings.WHATSAPP_VERIFY_TOKEN.get_secret_value()
    if not expected:
        raise AuthenticationError("WhatsApp webhooks are not configured.")

    if hub_mode != "subscribe" or not hub_verify_token or not hub_challenge:
        raise AuthenticationError("Invalid subscription request.")
    if not hmac.compare_digest(expected, hub_verify_token):
        logger.warning("whatsapp_verify_token_mismatch")
        raise AuthenticationError("Invalid verify token.")

    return Response(content=hub_challenge, media_type="text/plain")


@router.post(
    "/webhook",
    response_model=InboundResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_inbound(
    body: Annotated[bytes, Depends(verify_meta_signature)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InboundResult:
    """Ingest inbound WhatsApp messages.

    Always answers 202 once the signature verifies, even when nothing is
    ingested. Meta retries any non-2xx for hours, so returning an error for a
    message we chose to skip — a status receipt, an unsupported media type, an
    unroutable business number — would produce an infinite redelivery loop over
    something that will never succeed. What we could not process is logged.
    """
    try:
        payload: dict[str, Any] = json.loads(body)
    except ValueError:
        logger.warning("whatsapp_webhook_malformed_body")
        return InboundResult(status="accepted")

    messages = parse_inbound(payload)
    if not messages:
        # Status receipts and unsupported types land here. Not an error.
        return InboundResult(status="accepted")

    organization_id = _resolve_tenant(settings)
    if organization_id is None:
        logger.error("whatsapp_tenant_unresolved")
        return InboundResult(status="accepted")

    created_any = False
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await set_tenant_context(session, organization_id)
        service = InboundMessageService(session, organization_id)
        for message in messages:
            _row, created = await service.ingest_channel_message(message)
            created_any = created_any or created

    return InboundResult(status="accepted" if created_any else "duplicate")


def _resolve_tenant(settings: Settings) -> UUID | None:
    """Which workspace this number belongs to.

    Single-tenant today, so it is one configured id. Meta sends the business
    phone number the message was addressed to, which is the natural key when
    multi-tenant activation arrives — this becomes a lookup on that number, and
    the signature is already shaped for it.
    """
    configured = settings.WHATSAPP_ORGANIZATION_ID
    if not configured:
        return None
    try:
        return UUID(configured)
    except ValueError:
        logger.error("whatsapp_organization_id_invalid")
        return None
