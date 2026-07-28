"""The data classification framework — levels, privacy labels, and the rule
that ties them together.

Two closed registries: the sensitivity **levels** (public -> restricted, ordered
so "at least confidential" is an index comparison) and the privacy **labels** a
data asset can carry (PII, PHI, financial, …). Each label declares the *minimum*
classification it implies and whether it denotes personal data. The single rule
`classify` derives the recommended level from an asset's labels: the most
sensitive floor any of its labels demands. A label that is not registered cannot
be applied, so a typo is an error at the call site rather than an unclassifiable
asset.

Pure and monotonic: adding a more sensitive label never lowers the
recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Sensitivity levels, least to most restricted. Order is the comparison.
CLASSIFICATION_LEVELS: tuple[str, ...] = (
    "public",
    "internal",
    "confidential",
    "restricted",
)


@dataclass(frozen=True, slots=True)
class PrivacyLabel:
    key: str
    title: str
    description: str
    #: The lowest classification an asset carrying this label may hold.
    min_classification: str
    #: Whether the label denotes personal data (drives `contains_pii`).
    personal: bool = False
    #: Special-category / sensitive personal data (GDPR Art. 9-adjacent).
    sensitive: bool = False


def _l(
    key: str, title: str, description: str, min_classification: str,
    *, personal: bool = False, sensitive: bool = False,
) -> PrivacyLabel:
    return PrivacyLabel(
        key=key, title=title, description=description,
        min_classification=min_classification, personal=personal, sensitive=sensitive,
    )


PRIVACY_LABELS: dict[str, PrivacyLabel] = {
    label.key: label
    for label in (
        _l("public", "Public", "Information cleared for public release.", "public"),
        _l("contact", "Contact data", "Names, emails, phone numbers.",
           "confidential", personal=True),
        _l("pii", "Personal data", "Data identifying a natural person.",
           "confidential", personal=True),
        _l("sensitive_pii", "Special-category data",
           "Health, biometric, or other special-category personal data.",
           "restricted", personal=True, sensitive=True),
        _l("phi", "Protected health information",
           "Health information tied to an individual.",
           "restricted", personal=True, sensitive=True),
        _l("financial", "Financial data",
           "Account, card, or payment information.",
           "restricted", personal=True, sensitive=True),
        _l("credentials", "Secrets & credentials",
           "Passwords, tokens, keys.", "restricted"),
        _l("internal", "Internal", "Internal business information.", "internal"),
    )
}


def privacy_label(key: str) -> PrivacyLabel:
    """Look up a registered label. Raises `KeyError` for an unknown one."""
    try:
        return PRIVACY_LABELS[key]
    except KeyError as exc:
        raise KeyError(f"Unknown privacy label '{key}'.") from exc


def level_rank(level: str) -> int:
    """Index of a level for comparison. An unknown level sorts as `internal`,
    the safe baseline, rather than as `public`."""
    try:
        return CLASSIFICATION_LEVELS.index(level)
    except ValueError:
        return CLASSIFICATION_LEVELS.index("internal")


def max_level(a: str, b: str) -> str:
    return a if level_rank(a) >= level_rank(b) else b


#: The baseline an asset with no labels is recommended: not public, because an
#: unlabelled asset has not been assessed, and public is a decision, not a default.
DEFAULT_LEVEL = "internal"


def classify(labels: list[str] | tuple[str, ...] | frozenset[str]) -> str:
    """Recommend a classification level: the most sensitive floor the labels
    demand, or `internal` for an unlabelled asset.

    An unlabelled asset is `internal`, not `public` — it has not been assessed,
    and public is a decision. Once labels are present the recommendation is the
    highest floor among them, so an explicit `public` label does yield `public`
    while any personal-data label raises it. Unknown labels are ignored here (the
    service validates them); the rule itself never raises.
    """
    known = [
        label for key in labels if (label := PRIVACY_LABELS.get(key)) is not None
    ]
    if not known:
        return DEFAULT_LEVEL
    level = known[0].min_classification
    for label in known[1:]:
        level = max_level(level, label.min_classification)
    return level


def contains_personal_data(
    labels: list[str] | tuple[str, ...] | frozenset[str],
) -> bool:
    return any(
        (label := PRIVACY_LABELS.get(key)) is not None and label.personal
        for key in labels
    )


def contains_sensitive_data(
    labels: list[str] | tuple[str, ...] | frozenset[str],
) -> bool:
    return any(
        (label := PRIVACY_LABELS.get(key)) is not None and label.sensitive
        for key in labels
    )


def is_sensitive_level(level: str) -> bool:
    """A level at or above `confidential` — the sensitive-data registry's floor."""
    return level_rank(level) >= level_rank("confidential")


__all__ = [
    "CLASSIFICATION_LEVELS",
    "DEFAULT_LEVEL",
    "PRIVACY_LABELS",
    "PrivacyLabel",
    "classify",
    "contains_personal_data",
    "contains_sensitive_data",
    "is_sensitive_level",
    "level_rank",
    "max_level",
    "privacy_label",
]
