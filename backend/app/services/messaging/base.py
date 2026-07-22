"""Channel-agnostic messaging contract.

A `MessageChannel` knows how to say something to somebody over one transport
and how to recognise an address on it. Nothing above this layer knows whether a
conversation is email or WhatsApp — the inbox, the threading, the record
matching and the unread counts are identical, which is the whole reason
`conversations.channel` is a column rather than a table.

Two responsibilities that look unrelated and are not:

* `send` — outbound.
* `normalise_address` — the identity function for a thread. Get this wrong and
  the same person gets two conversations: `Sana@Example.com` and
  `sana@example.com` are one person, and `+1 (415) 555-0100` and
  `+14155550100` will be too once WhatsApp lands. Normalisation belongs to the
  channel because only the channel knows the rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """Something to say, with no opinion about how it travels."""

    to_address: str
    to_name: str | None
    body_text: str
    #: Email has one; WhatsApp does not. Channels that lack a subject ignore it
    #: rather than the caller having to know which is which.
    subject: str | None = None
    body_html: str | None = None
    #: The RFC 5322 Message-ID of what this replies to, when the channel
    #: supports threading.
    in_reply_to: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    provider_message_id: str
    channel: str
    #: What the provider called this message, if it differs from its own id —
    #: for email, the RFC Message-ID that will appear in the recipient's client.
    rfc_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """A message that arrived, already parsed out of whatever the provider sent.

    Deliberately provider-neutral: the webhook handler for each provider is
    responsible for producing one of these, so the ingestion pipeline — dedupe,
    thread, match, notify — is written once.
    """

    channel: str
    from_address: str
    from_name: str | None
    to_address: str
    body_text: str
    provider_message_id: str
    subject: str | None = None
    body_html: str | None = None
    rfc_message_id: str | None = None
    in_reply_to: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class MessagingError(Exception):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@runtime_checkable
class MessageChannel(Protocol):
    name: str

    def normalise_address(self, address: str) -> str:
        """The canonical form of an address on this channel.

        Used as a conversation's identity, so it must be stable and total: two
        spellings of the same address must produce the same string, and it must
        never raise on input that arrived from outside.
        """
        ...

    async def send(self, message: OutboundMessage) -> DeliveryResult: ...
