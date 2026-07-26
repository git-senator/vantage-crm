"""The property-content prompts: marketing language over a quality read the rules
produced.

Three single-purpose prompts, not one — a listing summary, a full marketing
description, and SEO suggestions — because they are three different pieces of
copy an agent asks for separately, and keeping them apart lets each be one
guarded, individually-priced `AIService` call rather than a bundle. All three
share the same discipline: the model is handed the listing's fenced, redacted
context plus the CRM's own strengths and pricing read, and asked to *write copy*.
It explains and markets; it does not invent facts and it takes no action.

Every one of these is grounded strictly in the data provided. A description that
invents a "recently renovated kitchen" is worse than no description — it is a
fair-housing and misrepresentation risk — so the rule against inventing facts is
first and load-bearing, not a nicety.
"""

from __future__ import annotations

from app.ai.prompts import PROMPTS, PromptTemplate

PROPERTY_PROMPT_VERSION = 1

_GROUNDING = (
    "Rules:\n"
    "- Use only the facts in the data. Do not invent features, figures, "
    "measurements, finishes, schools, or neighbourhood claims. If a detail is "
    "not given, do not state it. If a value is masked, leave it masked.\n"
    "- Do not make fair-housing-sensitive claims about the property's "
    "suitability for any group of people.\n"
    "- Be specific and concrete, drawn from the listing. No filler.\n"
    "- You are writing copy for the listing agent to review; you cannot change "
    "the listing or take any action."
)

PROPERTY_SUMMARY_SYSTEM = (
    "You are a real-estate CRM assistant writing a short, factual summary of one "
    "property listing for the agent who manages it. Write two or three sentences "
    "that capture what the property is and its most marketable, given strengths. "
    "Ground every claim in the data provided.\n\n" + _GROUNDING
)

PROPERTY_DESCRIPTION_SYSTEM = (
    "You are a real-estate copywriter drafting a listing description for the "
    "agent to review and edit. Using only the facts provided, write one or two "
    "engaging paragraphs (roughly 60-120 words) that present the property to "
    "prospective buyers, leading with its given strengths. Warm and professional, "
    "never overstated.\n\n" + _GROUNDING
)

PROPERTY_SEO_SYSTEM = (
    "You are a real-estate marketing assistant suggesting SEO for one listing, "
    "for the agent to review. Using only the facts provided, produce:\n"
    "1. A concise, search-friendly listing title (under ~60 characters).\n"
    "2. A meta description (roughly 150-160 characters).\n"
    "3. Six to ten relevant keywords or short phrases, comma-separated.\n\n"
    + _GROUNDING
)

PROPERTY_SUMMARY_PROMPT: PromptTemplate = PROMPTS.register(
    PromptTemplate(
        key="property_summary",
        version=PROPERTY_PROMPT_VERSION,
        system=PROPERTY_SUMMARY_SYSTEM,
        description="A grounded, factual summary of one property listing.",
    )
)

PROPERTY_DESCRIPTION_PROMPT: PromptTemplate = PROMPTS.register(
    PromptTemplate(
        key="property_description",
        version=PROPERTY_PROMPT_VERSION,
        system=PROPERTY_DESCRIPTION_SYSTEM,
        description="A drafted marketing description for one property listing.",
    )
)

PROPERTY_SEO_PROMPT: PromptTemplate = PROMPTS.register(
    PromptTemplate(
        key="property_seo",
        version=PROPERTY_PROMPT_VERSION,
        system=PROPERTY_SEO_SYSTEM,
        description="SEO title, meta description and keywords for one listing.",
    )
)

#: The content kinds the endpoint accepts, each mapped to its prompt and the
#: feature tag its egress is recorded under. One entry adds a content kind.
CONTENT_PROMPTS: dict[str, tuple[PromptTemplate, str]] = {
    "summary": (PROPERTY_SUMMARY_PROMPT, "property_summary"),
    "description": (PROPERTY_DESCRIPTION_PROMPT, "property_description"),
    "seo": (PROPERTY_SEO_PROMPT, "property_seo"),
}


__all__ = [
    "CONTENT_PROMPTS",
    "PROPERTY_DESCRIPTION_PROMPT",
    "PROPERTY_PROMPT_VERSION",
    "PROPERTY_SEO_PROMPT",
    "PROPERTY_SUMMARY_PROMPT",
]
