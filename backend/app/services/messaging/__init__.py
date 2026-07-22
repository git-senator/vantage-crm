"""Messaging channels.

`build_channel` is the only place that maps a channel name to an adapter.
Business logic takes a `MessageChannel`, so adding WhatsApp in Phase 3.6 is an
adapter plus one arm here — the inbox, the threading, the record matching and
the unread counts do not change, because none of them ever knew which channel
they were looking at.
"""

from __future__ import annotations

from app.services.messaging.base import (
    DeliveryResult,
    InboundMessage,
    MessageChannel,
    MessagingError,
    OutboundMessage,
)
from app.services.messaging.email_channel import EmailChannel
from app.services.messaging.whatsapp_channel import WhatsAppChannel, parse_inbound

__all__ = [
    "DeliveryResult",
    "EmailChannel",
    "InboundMessage",
    "MessageChannel",
    "MessagingError",
    "OutboundMessage",
    "WhatsAppChannel",
    "build_channel",
    "parse_inbound",
]

_channels: dict[str, MessageChannel] = {}


def build_channel(channel: str) -> MessageChannel:
    """Resolve an adapter, cached per process.

    Cached because an adapter owns a provider client with a connection pool,
    and rebuilding one per message would negate it.
    """
    if channel in _channels:
        return _channels[channel]

    match channel:
        case "email":
            adapter: MessageChannel = EmailChannel()
        case "whatsapp":
            adapter = WhatsAppChannel()
        case "sms":
            # Declared in the schema, deliberately not implemented. Raising is
            # the honest answer: a silent no-op would let a user believe a
            # message was sent.
            raise MessagingError(
                "The sms channel is not available yet.", retryable=False
            )
        case unknown:
            raise MessagingError(f"Unknown channel: {unknown}", retryable=False)

    _channels[channel] = adapter
    return adapter


def reset_channels() -> None:
    """Drop the adapter cache. Tests use this; nothing else should."""
    _channels.clear()
