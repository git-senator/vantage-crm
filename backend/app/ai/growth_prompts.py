"""The growth-briefing prompt: language over a business-health read the rules
produced.

The same division as the other insight prompts: the model is handed a growth
score, the signals that produced it, the revenue signals, the pipeline insights,
and the risks and recommended actions — all computed by the CRM from aggregate
analytics — and asked to write a short leadership briefing. It explains the
numbers; it does not produce them, and it takes no action.

There is no per-record context here and therefore nothing fenced: a growth
briefing works from derived aggregates only, never raw customer records, so the
whole input is trusted, CRM-authored analysis.
"""

from __future__ import annotations

from app.ai.prompts import PROMPTS, PromptTemplate

GROWTH_BRIEFING_PROMPT_VERSION = 1

GROWTH_BRIEFING_SYSTEM = (
    "You are a real-estate CRM assistant writing a brief business-health "
    "briefing for the owner or manager of a brokerage. You are given a growth "
    "score, the signals behind it, revenue signals, pipeline insights, and the "
    "risks and recommended actions — all computed by the CRM from its own "
    "analytics, not by you.\n\n"
    "Write two short things:\n"
    "1. A three- or four-sentence read on how the business is doing this period "
    "and what is driving it, grounded strictly in the signals you were given.\n"
    "2. The two or three most important priorities, drawn from the recommended "
    "actions provided.\n\n"
    "Rules:\n"
    "- Do not invent facts, figures, or trends. Use only what is in the data.\n"
    "- Do not contradict the growth score; explain it.\n"
    "- Be specific and concise. No preamble.\n"
    "- You cannot take actions or change any data — you advise the manager."
)

GROWTH_BRIEFING_PROMPT: PromptTemplate = PROMPTS.register(
    PromptTemplate(
        key="growth_briefing",
        version=GROWTH_BRIEFING_PROMPT_VERSION,
        system=GROWTH_BRIEFING_SYSTEM,
        description="A grounded business-health briefing over the growth read.",
    )
)


__all__ = [
    "GROWTH_BRIEFING_PROMPT",
    "GROWTH_BRIEFING_PROMPT_VERSION",
    "GROWTH_BRIEFING_SYSTEM",
]
