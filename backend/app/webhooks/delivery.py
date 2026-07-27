"""The HTTP send — the one part of delivery that talks to the outside world.

Kept out of the service and off any database transaction: a delivery POST can
take as long as the timeout allows, and holding a tenant transaction open for
that is how a slow consumer becomes a connection-pool outage. The job reads what
it needs, commits, sends here, then opens a fresh transaction to record the
result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    #: True on a 2xx. Everything else — 4xx, 5xx, timeout, refused — is a
    #: failure that retries.
    ok: bool
    #: The HTTP status, or None when the request never got a response.
    status_code: int | None
    #: A truncated response body, kept for the delivery record.
    body_snippet: str | None
    #: A transport-level error string when there was no response.
    error: str | None


def serialize(payload: dict[str, object]) -> bytes:
    """Canonical bytes for both signing and sending.

    Signed and sent must be byte-identical, so the body is serialised once here
    and both the signature and the request use the result.
    """
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


async def post(
    url: str,
    *,
    body: bytes,
    headers: dict[str, str],
    timeout_seconds: float,
    snippet_bytes: int,
) -> DeliveryResult:
    """POST a signed body to a consumer, classifying the outcome."""
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("webhook_delivery_transport_error", extra={"error": str(exc)})
        return DeliveryResult(
            ok=False, status_code=None, body_snippet=None, error=str(exc)[:500]
        )

    snippet = response.text[:snippet_bytes] if response.text else None
    ok = 200 <= response.status_code < 300
    return DeliveryResult(
        ok=ok, status_code=response.status_code, body_snippet=snippet, error=None
    )
