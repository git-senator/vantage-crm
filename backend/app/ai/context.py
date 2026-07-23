"""Context builders: turning scoped CRM data into prompt content.

This is where the flagship AI risk is contained. SECURITY.md §5 states it
plainly: retrieval must filter by `organization_id` **and** the caller's scope
predicate **before** anything reaches a model, using the same scope resolver the
list endpoints use — never a second one. A builder that fetched a record the
caller cannot read and handed it to a model would be a broken-access-control bug
with a language model for a delivery mechanism.

So a concrete builder (leads in 6.3, deals in 6.4, and so on) does not query
freely. It goes through the entity's own service or repository under the
caller's `AuthorizationContext`, exactly as a request handler does, so a record
the caller could not open in the UI is one the model never sees either. This
module gives those builders the safe primitives; it deliberately does not fetch
anything itself, because "fetch some context" with no entity and no scope is the
shape of the mistake.

Two primitives:

  * `field(label, value)` — a piece of CRM text, redacted for egress and fenced
    as untrusted so the model treats it as data (see `app/ai/prompts.py`).
  * `instruction(text)` — text we authored: the task, a question. Trusted,
    unredacted, unfenced.

The default is untrusted-and-redacted, because the safe default is the one that
makes forgetting expensive rather than silent.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.ai.prompts import ContentBlock
from app.ai.redaction import redact


def field(label: str, value: str | None) -> ContentBlock | None:
    """A CRM field as untrusted, redacted content. `None`/blank yields nothing.

    Returns `None` for an empty value so a builder can drop absent fields
    without threading conditionals — a prompt full of "notes: (none)" lines
    wastes tokens and teaches the model nothing.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return ContentBlock(text=redact(text), label=label, trusted=False)


def instruction(text: str) -> ContentBlock:
    """Text we authored — a task, a question. Trusted, so not fenced."""
    return ContentBlock(text=text, label="instruction", trusted=True)


def fields(pairs: Iterable[tuple[str, str | None]]) -> list[ContentBlock]:
    """Several CRM fields at once, dropping the empty ones."""
    blocks = (field(label, value) for label, value in pairs)
    return [block for block in blocks if block is not None]


class ContextBuilder:
    """Base for entity-specific builders.

    Holds the caller's authorization context so a subclass has no excuse to
    resolve scope any other way than through it. A subclass overrides `build`
    to fetch its entity **under this scope** and assemble blocks with the
    primitives above.

    Kept minimal on purpose: the shared value here is the discipline (scope in,
    redacted blocks out), not a framework of hooks nobody needs yet.
    """

    #: A short, stable label for the feature, used in job metadata and cost
    #: attribution. Overridden per builder.
    feature: str = "context"

    def build(self) -> list[ContentBlock]:  # pragma: no cover - abstract
        raise NotImplementedError


__all__ = ["ContextBuilder", "field", "fields", "instruction"]
