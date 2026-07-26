"""The deal-insight prompt: language over a health read the rules produced.

Same division as the lead-insight prompt (`app/ai/lead_prompts.py`): the model is
handed a health score, a win probability, the signals and risks that produced
them — all computed by the CRM — plus the deal's own fenced, redacted context,
and asked to write a short summary and next steps. It explains the numbers; it
does not produce them, and it takes no action.
"""

from __future__ import annotations

from app.ai.prompts import PROMPTS, PromptTemplate

DEAL_INSIGHT_PROMPT_VERSION = 1

DEAL_INSIGHT_SYSTEM = (
    "You are a real-estate CRM assistant writing a brief on one deal for the "
    "agent who owns it. You are given a health score, an inferred win "
    "probability with the factors behind it, the deal's stage and timing, and "
    "the risks and recommended actions — all computed by the CRM, not by you.\n\n"
    "Write two short things:\n"
    "1. A two- or three-sentence read on where the deal stands and what is "
    "helping or hurting it, grounded strictly in the signals you were given.\n"
    "2. The one or two most important next steps to move it forward, drawn from "
    "the recommended actions provided.\n\n"
    "Rules:\n"
    "- Do not invent facts, figures, names, dates, or amounts. Use only what is "
    "in the data. If details are masked, leave them masked.\n"
    "- Do not contradict the health score or the win probability; explain them.\n"
    "- Be specific and concise. No preamble.\n"
    "- You cannot take actions or change the deal — you recommend to the agent."
)

DEAL_INSIGHT_PROMPT: PromptTemplate = PROMPTS.register(
    PromptTemplate(
        key="deal_insight",
        version=DEAL_INSIGHT_PROMPT_VERSION,
        system=DEAL_INSIGHT_SYSTEM,
        description="A grounded narrative summary and next steps for one deal.",
    )
)


__all__ = [
    "DEAL_INSIGHT_PROMPT",
    "DEAL_INSIGHT_PROMPT_VERSION",
    "DEAL_INSIGHT_SYSTEM",
]
