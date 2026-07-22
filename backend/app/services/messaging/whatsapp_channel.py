"""The WhatsApp channel, over the Meta Cloud API.

This is the payoff for Phase 3.4's decision to make `channel` a column rather
than a table: the inbox, the threading, the record matching, the unread counts
and the outbound job are all unchanged. What is here is an adapter and a webhook
translator, and nothing above them knows the difference.

Two things about WhatsApp are genuinely different from email and are modelled
rather than papered over.

**Addresses are phone numbers, and normalisation is E.164.** `+1 (415) 555-0100`
and `+14155550100` are the same person, so both must produce the same
conversation identity. Meta itself returns bare digits with no `+`, which is a
third spelling of the same thing.

**There is a 24-hour session window.** Outside 24 hours of a customer's last
inbound message, WhatsApp permits only pre-approved template messages — free-form
text is rejected by the API. That is a platform rule, not a preference, so this
adapter refuses the send with a clear, terminal error rather than letting an
agent watch a message sit `queued` and then fail with a Meta error code. Sending
templates is a product decision (they must be registered and approved in advance)
and is deliberately out of scope here; what ships is an honest boundary.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.messaging.base import (
    DeliveryResult,
    InboundMessage,
    MessagingError,
    OutboundMessage,
)

logger = get_logger(__name__)

#: Anything that is not a digit. Phone numbers arrive spelled a dozen ways.
_NON_DIGITS = re.compile(r"\D+")

#: Meta error codes that will not succeed on a retry. 131047 is the
#: outside-the-session-window rejection; the rest are configuration or content
#: faults. Retrying any of them burns the queue's budget for nothing.
_TERMINAL_CODES = frozenset({100, 131_026, 131_047, 131_051, 132_000, 190})


class WhatsAppChannel:
    name = "whatsapp"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    # ------------------------------------------------------------ identity

    def normalise_address(self, address: str) -> str:
        """E.164 without the `+`, matching what Meta sends inbound.

        Storing Meta's own spelling means an inbound message and an outbound
        one to the same person land on the same conversation without a
        translation step that somebody would eventually forget.

        Total by contract: this runs on data that arrived from outside, so it
        never raises. A string with no digits normalises to empty, and the
        caller rejects that as a missing recipient.
        """
        return _NON_DIGITS.sub("", address.strip())

    @staticmethod
    def looks_like_number(address: str) -> bool:
        digits = _NON_DIGITS.sub("", address)
        # E.164 permits 8 to 15 digits including the country code. Shorter is a
        # short code or a typo; longer is not a phone number.
        return 8 <= len(digits) <= 15

    # ------------------------------------------------------------ outbound

    async def send(self, message: OutboundMessage) -> DeliveryResult:
        settings = self._settings
        token = settings.WHATSAPP_ACCESS_TOKEN.get_secret_value()
        if not (token and settings.WHATSAPP_PHONE_NUMBER_ID):
            raise MessagingError(
                "WhatsApp is not configured for this deployment.", retryable=False
            )

        to = self.normalise_address(message.to_address)
        if not self.looks_like_number(to):
            raise MessagingError(
                f"{message.to_address} is not a valid phone number.", retryable=False
            )

        url = (
            f"{settings.WHATSAPP_API_BASE.rstrip('/')}/"
            f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            # `preview_url` off: link previews fetch the target from Meta's
            # servers, which leaks that a link was sent and to whom.
            "text": {"preview_url": False, "body": message.body_text},
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            raise MessagingError(f"WhatsApp transport failure: {exc}") from exc

        if response.status_code >= 400:
            raise self._error_from(response)

        body = response.json()
        messages = body.get("messages") or [{}]
        provider_id = str(messages[0].get("id", ""))
        if not provider_id:  # pragma: no cover — Meta always returns one
            raise MessagingError("WhatsApp accepted the message without an id.")

        logger.info("whatsapp_message_sent", extra={"provider_message_id": provider_id})
        return DeliveryResult(provider_message_id=provider_id, channel=self.name)

    def _error_from(self, response: httpx.Response) -> MessagingError:
        """Translate Meta's error body into a retryable/terminal decision."""
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}

        code = int(error.get("code", 0) or 0)
        message = str(error.get("message", response.text))[:400]

        if code == 131_047:
            # The one Meta error a user can actually act on.
            return MessagingError(
                "WhatsApp only allows free-form replies within 24 hours of the "
                "customer's last message. This conversation is outside that "
                "window.",
                retryable=False,
            )

        retryable = code not in _TERMINAL_CODES and response.status_code >= 500
        logger.error(
            "whatsapp_send_failed",
            extra={
                "status": response.status_code,
                "meta_code": code,
                "retryable": retryable,
            },
        )
        return MessagingError(f"WhatsApp rejected the message: {message}", retryable=retryable)


def parse_inbound(payload: dict[str, Any]) -> list[InboundMessage]:
    """Translate a Meta webhook body into channel-neutral messages.

    Meta batches: one webhook may carry several entries, each with several
    changes, each with several messages. Returning a list rather than a single
    message is not defensive — it is the actual shape of the API.

    Statuses (delivered/read receipts) arrive through the same endpoint and are
    deliberately ignored here: they carry no message body, and treating them as
    messages would fill a thread with empty rows. Wiring them to update
    `messages.status` is a follow-up with its own dedupe question.

    Everything is read defensively because this is untrusted input that has
    already passed signature verification but not sanity checking. A malformed
    entry is skipped rather than raising, so one bad message in a batch cannot
    reject the whole delivery and cause Meta to retry the good ones forever.
    """
    parsed: list[InboundMessage] = []

    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            business_number = str(metadata.get("display_phone_number", ""))

            # `contacts` carries the sender's profile name, keyed by wa_id.
            names = {
                str(contact.get("wa_id", "")): (
                    (contact.get("profile") or {}).get("name")
                )
                for contact in (value.get("contacts") or [])
            }

            for message in value.get("messages", []) or []:
                if message.get("type") != "text":
                    # Media, location, reactions, interactive replies. Each
                    # needs its own handling — a media message means fetching
                    # the object from Meta and storing it — and pretending an
                    # image is an empty text message is worse than skipping it.
                    logger.info(
                        "whatsapp_unsupported_message_type",
                        extra={"type": message.get("type")},
                    )
                    continue

                sender = str(message.get("from", ""))
                body = str((message.get("text") or {}).get("body", ""))
                provider_id = str(message.get("id", ""))
                if not (sender and provider_id):
                    continue

                parsed.append(
                    InboundMessage(
                        channel="whatsapp",
                        from_address=sender,
                        from_name=names.get(sender),
                        to_address=business_number,
                        body_text=body,
                        provider_message_id=provider_id,
                        metadata={"wa_timestamp": str(message.get("timestamp", ""))},
                    )
                )

    return parsed
