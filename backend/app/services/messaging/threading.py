"""RFC 5322 threading: Message-ID, In-Reply-To and References.

This closes the gap `email_channel.py` documented from Phase 3.5 onwards: a
reply sent from the CRM threaded correctly *in the CRM* but started a fresh
chain in the recipient's own mail client, because the simple send API has no way
to set the headers that make a reply a reply.

## The three headers, and what each actually does

`Message-ID` identifies this message. **We generate it ourselves** rather than
letting the provider assign one, and that is the decision the rest of this
depends on: a reply must reference the id of the message it answers, so the id
has to exist and be recorded *before* the send, not be discovered afterwards
from a provider response. A provider-assigned id is unknowable at compose time,
which makes threading a chain of guesses.

`In-Reply-To` is the immediate parent. One id.

`References` is the whole ancestry, oldest first. This is what mail clients
actually thread on — Gmail and Outlook both walk it — and `In-Reply-To` alone
produces threads that fragment whenever one message in the middle goes missing.

## The length problem

A long thread's References header grows without bound, and mail servers reject
oversized headers. RFC 5322 says a client may trim, and the convention every
implementation follows is to keep the **first** reference and the most recent
few: the first anchors the thread's identity, the recent ones connect it to what
is in front of the reader. Dropping from the middle is what preserves both.
"""

from __future__ import annotations

import uuid

from app.core.logging import get_logger

logger = get_logger(__name__)

#: How many references survive trimming: the root plus this many recent ones.
#: Eight keeps the header comfortably inside the 998-octet line limit even with
#: long ids, and no client threads on ancestry deeper than that anyway.
MAX_REFERENCES = 9

#: A Message-ID longer than this is malformed or hostile — the column is 500,
#: and anything approaching it is not a real id from a real mail system.
MAX_MESSAGE_ID_LENGTH = 500


def generate_message_id(domain: str) -> str:
    """A fresh RFC 5322 Message-ID.

    `<uuid4@domain>`. UUID4 rather than a timestamp-and-hostname: the id must be
    globally unique and must not leak the sending host, the queue position, or
    how many messages this workspace has sent — all of which the traditional
    `<timestamp.counter@host>` form does.
    """
    host = domain.strip().lstrip("@") or "localhost"
    return f"<{uuid.uuid4()}@{host}>"


def domain_of(address: str) -> str:
    """The domain to mint ids under: the sender's own.

    A Message-ID whose domain does not match the sending domain is a
    deliverability signal — DMARC-aligned receivers treat mismatched ids as
    weakly suspicious, and there is no reason to spend that credibility.
    """
    _local, at, domain = address.rpartition("@")
    # `rpartition` puts the *whole* string in the third slot when there is no
    # separator, so without checking `at` a malformed sender would become a
    # Message-ID domain of "not-an-address" — a header that is worse than the
    # fallback, because it looks deliberate.
    if not at:
        return "localhost"
    return domain.strip().lower() or "localhost"


def normalise_message_id(value: str | None) -> str | None:
    """Clean an id that arrived from outside.

    Angle brackets are added if missing, since some senders omit them and a
    bare id will not match one that has them. Anything oversized or containing
    whitespace is dropped rather than repaired: an unusable header is better
    than one that breaks the message it is attached to.
    """
    if not value:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_MESSAGE_ID_LENGTH:
        return None
    if any(char.isspace() for char in candidate):
        return None
    if not candidate.startswith("<"):
        candidate = f"<{candidate}"
    if not candidate.endswith(">"):
        candidate = f"{candidate}>"
    return candidate


def parse_references(value: str | None) -> list[str]:
    """A References header into ids.

    The header is whitespace-separated, and real-world senders use every
    combination of spaces, tabs and folded lines — so splitting on whitespace
    and re-normalising each token is more reliable than a strict parse.
    """
    if not value:
        return []
    out: list[str] = []
    for token in value.split():
        cleaned = normalise_message_id(token)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def build_references(
    parent_references: str | list[str] | None, parent_message_id: str | None
) -> list[str]:
    """The References chain for a reply.

    The parent's own chain, then the parent itself. That ordering is the whole
    specification: References is the path from the thread's root to the message
    being answered, and appending the parent last is what extends it by one.
    """
    chain = (
        parse_references(parent_references)
        if isinstance(parent_references, str) or parent_references is None
        else [ref for ref in (normalise_message_id(r) for r in parent_references) if ref]
    )

    parent = normalise_message_id(parent_message_id)
    if parent and parent not in chain:
        chain.append(parent)

    return trim_references(chain)


def trim_references(chain: list[str]) -> list[str]:
    """Keep the root and the most recent ids; drop the middle.

    Not the *last* N: dropping the root would sever the thread's identity, and
    a client that has the root cached would stop matching. The convention every
    mail implementation follows is first-plus-recent, and it is followed here
    for the same reason — interoperating with what receivers actually do beats
    interoperating with a stricter reading of the RFC.
    """
    if len(chain) <= MAX_REFERENCES:
        return chain
    return [chain[0], *chain[-(MAX_REFERENCES - 1) :]]


def render_references(chain: list[str]) -> str | None:
    """The header value. `None` when there is nothing to reference."""
    return " ".join(chain) if chain else None


def threading_headers(
    *,
    message_id: str,
    in_reply_to: str | None,
    references: list[str],
) -> dict[str, str]:
    """The headers to attach to an outbound message.

    Returned as a plain dict so the provider adapter decides how to render them
    — SES needs raw MIME, another provider may take a headers map, and neither
    concern belongs in the channel that composes the message.
    """
    headers = {"Message-ID": message_id}

    parent = normalise_message_id(in_reply_to)
    if parent:
        headers["In-Reply-To"] = parent

    rendered = render_references(references)
    if rendered:
        headers["References"] = rendered

    return headers


__all__ = [
    "MAX_REFERENCES",
    "build_references",
    "domain_of",
    "generate_message_id",
    "normalise_message_id",
    "parse_references",
    "render_references",
    "threading_headers",
    "trim_references",
]
