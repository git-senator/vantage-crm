"""Social / omnichannel channels — inbound only, for now.

Website forms, Telegram, Instagram, Facebook and Google all reach the CRM
through the AI lead orchestrator: it normalises each provider's payload and
posts an inbound message. From the inbox's point of view they are the same as
email or WhatsApp — a thread keyed by (channel, external identity) — which is
why they need nothing more than an identity function to join the substrate.

Outbound is deliberately not implemented. Replying on these channels means a
real provider integration (a Telegram bot token, a Meta send API), which lands
with the channel move to the VPS; until then a `send` is refused loudly rather
than silently dropped, exactly as the SMS arm is.

One adapter serves every one of them because the identity rule is the same: an
external handle or address, trimmed and lowercased, with internal whitespace
collapsed so `@Ana Volkova` and `@ana volkova` are one person and not two.
"""

from __future__ import annotations

from app.services.messaging.base import (
    DeliveryResult,
    MessagingError,
    OutboundMessage,
)

#: The channels this adapter answers for. WhatsApp and email keep their own
#: adapters because they carry provider clients and threading rules this one has
#: no business knowing about.
SOCIAL_CHANNELS = frozenset(
    {"website", "telegram", "instagram", "facebook", "google", "youtube"}
)


class SocialChannel:
    """An inbound-only adapter, parameterised by which channel it speaks for.

    `build_channel` constructs one per name and caches it, so the `name` a
    delivery result would carry is the real channel, not a generic label.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def normalise_address(self, address: str) -> str:
        """The canonical form of a handle or address on a social channel.

        Total and stable: it never raises, and two spellings that a human would
        read as the same account collapse to one string — the property a
        conversation's identity depends on. It does not try to validate; a
        website lead with a malformed handle is still a lead, and storing it as
        the thread identity is better than dropping the enquiry.
        """
        return " ".join(address.strip().lower().split())

    async def send(self, message: OutboundMessage) -> DeliveryResult:
        raise MessagingError(
            f"Outbound is not available on the {self.name} channel yet.",
            retryable=False,
        )
