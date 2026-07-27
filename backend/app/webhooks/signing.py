"""HMAC signing for outbound webhooks.

The mirror image of the inbound verification in `app/api/v1/webhooks.py`, and it
gets the same things right so a receiver can verify the same way we do:

  * **The signature covers the raw bytes plus a timestamp**, joined as
    ``{timestamp}.{body}``. Signing a re-serialised model would produce a
    different string than the receiver sees, and the timestamp bounds replay.
  * **The timestamp is a separate header**, so the receiver reconstructs the
    signed string exactly and can reject a stale delivery.
  * **The signature is prefixed with its scheme** (``sha256=``), leaving room to
    rotate the algorithm without breaking existing verifiers.

The secret is per-endpoint and lives encrypted at rest; it reaches this module
only as plaintext held for the duration of a single delivery.
"""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-Vantage-Signature"
TIMESTAMP_HEADER = "X-Vantage-Timestamp"
EVENT_HEADER = "X-Vantage-Event"
#: Unique per delivery — a receiver keys its own dedupe on this.
DELIVERY_HEADER = "X-Vantage-Delivery"


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Return the `sha256=<hex>` signature for a delivery body."""
    payload = timestamp.encode() + b"." + body
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_headers(
    *, secret: str, timestamp: str, body: bytes, event_type: str, delivery_id: str
) -> dict[str, str]:
    """The full header set for a delivery POST."""
    return {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: sign(secret, timestamp, body),
        TIMESTAMP_HEADER: timestamp,
        EVENT_HEADER: event_type,
        DELIVERY_HEADER: delivery_id,
        "User-Agent": "Vantage-Webhooks/1.0",
    }
