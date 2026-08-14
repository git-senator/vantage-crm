"""Instagram Direct and Facebook Messenger, over Meta's Send API.

One adapter for both because Meta made them one product: the same endpoint, the
same payload, the same error envelope. What differs is which id the message is
sent *from* (an Instagram professional account, or a Facebook Page) and which
token authorises it — so the adapter is parameterised by channel name and reads
the pair from settings.

Three things about this channel are genuinely different from WhatsApp and are
modelled rather than papered over.

**Addresses are opaque scoped ids, not handles.** Meta does not give out the
counterparty's `@username`; it gives a per-app scoped id (IGSID for Instagram,
PSID for Messenger) that identifies the same person only to us. Replying
requires that id — a handle is not an address here, however natural it looks in
an inbox. Conversations that arrived through the orchestrator carrying only a
handle therefore cannot be answered through the API, and `send` says so plainly
instead of failing at Meta with an opaque code.

**There is a 24-hour window**, as on WhatsApp: outside 24 hours of the person's
last message, free-form replies are refused and only specific tagged message
types are permitted. Sending those is a product decision, not a transport one,
and is deliberately out of scope. What ships is an honest boundary.

**Instagram keeps its app.** Unlike WhatsApp, connecting this channel does not
take the account away from the phone: the Instagram app keeps working and the
API sees the same conversations. That is why this channel is the cheap one to
adopt first, and it is worth not breaking.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.messaging.base import (
    DeliveryResult,
    MessagingError,
    OutboundMessage,
)

logger = get_logger(__name__)

#: The channels this adapter speaks for, and where each finds its credentials.
#: Kept as data so adding Messenger later is a row, not a branch.
META_CHANNELS: dict[str, tuple[str, str]] = {
    "instagram": ("INSTAGRAM_ACCOUNT_ID", "INSTAGRAM_ACCESS_TOKEN"),
    "facebook": ("FACEBOOK_PAGE_ID", "FACEBOOK_ACCESS_TOKEN"),
}


class MetaMessagingChannel:
    """Outbound for one Meta messaging surface."""

    def __init__(self, channel: str, settings: Settings | None = None) -> None:
        if channel not in META_CHANNELS:
            raise MessagingError(f"Not a Meta channel: {channel}", retryable=False)
        self.name = channel
        self._settings = settings or get_settings()

    # ------------------------------------------------------------ identity

    def normalise_address(self, address: str) -> str:
        """Trim, and nothing else.

        A scoped id is an opaque token: case is not ours to change, and the
        digits are not a number we should reformat. Total by contract — this
        runs on data that arrived from outside and never raises.
        """
        return address.strip()

    @staticmethod
    def is_scoped_id(address: str) -> bool:
        """Meta's scoped ids are long digit strings; handles are not.

        The distinction matters because a conversation keyed by `@ana_volkova`
        looks perfectly sendable right up until Meta rejects it, and the
        rejection reads like a permissions problem rather than what it is.
        """
        return address.isdigit() and len(address) >= 6

    # ------------------------------------------------------------ outbound

    def _credentials(self) -> tuple[str, str]:
        account_field, token_field = META_CHANNELS[self.name]
        account = str(getattr(self._settings, account_field, "") or "")
        secret = getattr(self._settings, token_field, None)
        token = secret.get_secret_value() if secret is not None else ""
        if not (account and token):
            raise MessagingError(
                f"{self.name.title()} is not configured for this deployment.",
                retryable=False,
            )
        return account, token

    async def send(self, message: OutboundMessage) -> DeliveryResult:
        account, token = self._credentials()

        to = self.normalise_address(message.to_address)
        if not self.is_scoped_id(to):
            raise MessagingError(
                f"This {self.name} conversation has no Meta id — only the handle "
                f"«{message.to_address}». Meta requires its own recipient id to "
                "send, so this thread can be answered in the app but not from "
                "here. Replies work on conversations that arrived through the "
                "connected webhook.",
                retryable=False,
            )

        url = f"{self._settings.META_API_BASE.rstrip('/')}/{account}/messages"
        payload: dict[str, Any] = {
            "recipient": {"id": to},
            "message": {"text": message.body_text},
            # The default. Named explicitly because the alternatives (tagged
            # messages that reach outside the 24-hour window) are a deliberate
            # product decision, and a silent default would hide that.
            "messaging_type": "RESPONSE",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            raise MessagingError(f"{self.name} transport failure: {exc}") from exc

        if response.status_code >= 400:
            raise self._error_from(response)

        body = response.json()
        provider_id = str(body.get("message_id", "") or "")
        if not provider_id:
            raise MessagingError(f"{self.name} accepted the message without an id.")

        logger.info(
            "meta_message_sent",
            extra={"channel": self.name, "provider_message_id": provider_id},
        )
        return DeliveryResult(provider_message_id=provider_id, channel=self.name)

    def _error_from(self, response: httpx.Response) -> MessagingError:
        """Decide retryable vs terminal from Meta's error envelope.

        Deliberately coarse. Meta's numeric subcodes are many, undocumented in
        places and versioned; branching on a list I am not certain of would
        produce confident wrong answers — a message retried forever, or one
        abandoned that would have gone through. Transport class is something
        HTTP already tells us honestly: 429 and 5xx are worth another attempt,
        the rest are ours to fix.
        """
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}

        detail = str(error.get("error_user_msg") or error.get("message") or response.text)[:400]
        retryable = response.status_code == 429 or response.status_code >= 500

        logger.error(
            "meta_send_failed",
            extra={
                "channel": self.name,
                "status": response.status_code,
                "code": error.get("code"),
                "subcode": error.get("error_subcode"),
                "retryable": retryable,
            },
        )
        return MessagingError(
            f"{self.name.title()} rejected the message: {detail}", retryable=retryable
        )
