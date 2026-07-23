"""PII minimisation before egress.

Every string of CRM text that leaves for a model passes through here first. This
is the SECURITY.md §5 "data egress" control made concrete: the model is a third
party, and a lead's email or phone number is not something that has to cross
that boundary for the model to do its job — a summary of a lead reads exactly as
well with the contact details masked.

**This is minimisation, not anonymisation.** It reduces what is exposed; it does
not promise the text is unidentifiable afterwards, and nothing downstream should
treat a redacted string as safe to publish. The point is that the obvious direct
identifiers — email, phone, and long digit runs that are plausibly card or
account numbers — do not need to be in the prompt, so they are removed before
they can be.

Redaction is applied to *content*, never to instructions: the system prompt is
authored by us and carries no customer PII, so it is left untouched.
"""

from __future__ import annotations

import re

#: Email addresses. Deliberately broad — over-masking a false positive costs a
#: model a little context; under-masking a real address sends it to a third
#: party, and the two errors are not symmetric.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

#: Phone-shaped runs: an optional +, then 7-15 digits with spaces, dashes,
#: dots or parens between them. Tuned to catch international and domestic forms
#: without eating ordinary prose.
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")

#: A run of 13+ digits — long enough to be a card or account number and too long
#: to be a price, a year, or a street number. Checked before phone so a 16-digit
#: card does not get the softer phone mask.
_LONG_DIGITS = re.compile(r"\b\d{13,}\b")

EMAIL_MASK = "[email]"
PHONE_MASK = "[phone]"
NUMBER_MASK = "[number]"


def redact(text: str) -> str:
    """Mask direct identifiers in a piece of CRM text.

    Order matters: long digit runs first (a card number is not a phone number),
    then email, then phone. Idempotent — running it twice masks nothing new,
    because a mask contains no identifier.
    """
    if not text:
        return text
    masked = _LONG_DIGITS.sub(NUMBER_MASK, text)
    masked = _EMAIL.sub(EMAIL_MASK, masked)
    masked = _PHONE.sub(PHONE_MASK, masked)
    return masked


def redact_mapping(values: dict[str, str]) -> dict[str, str]:
    """Redact every value in a mapping, leaving keys untouched.

    Keys are field names we chose (`"notes"`, `"description"`); values are the
    customer's text. Only the values can carry PII.
    """
    return {key: redact(value) for key, value in values.items()}


__all__ = [
    "EMAIL_MASK",
    "NUMBER_MASK",
    "PHONE_MASK",
    "redact",
    "redact_mapping",
]
