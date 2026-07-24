"""The lead-insight prompt: language over a score the rules already computed.

The model's job here is narrow and stated in the prompt: take a score and its
reasons — which the deterministic engine produced, not the model — and write a
short, grounded summary and a phrased set of next steps. It does not invent the
score, the temperature, or a number; those arrive as data it must respect. This
is the division that keeps scoring deterministic while still giving an agent
prose to read: the numbers are rules, the words are the model.

Registered and versioned in the shared registry like every other prompt, so the
wording is one edit and the version travels onto the ledger row.
"""

from __future__ import annotations

from app.ai.prompts import PROMPTS, PromptTemplate

LEAD_INSIGHT_PROMPT_VERSION = 1

LEAD_INSIGHT_SYSTEM = (
    "You are a real-estate CRM assistant writing a brief on one lead for the "
    "agent who owns it. You are given a score, a temperature, a qualification, "
    "and the specific signals, risks and recommended actions that produced "
    "them — all computed by the CRM, not by you.\n\n"
    "Write two short things:\n"
    "1. A two- or three-sentence summary of where this lead stands and why, "
    "grounded strictly in the signals you were given.\n"
    "2. The two or three most important next steps, phrased as an agent would "
    "say them, drawn from the recommended actions provided.\n\n"
    "Rules:\n"
    "- Do not invent facts, figures, names, or contact details. Use only what "
    "is in the data. If contact details are masked, leave them masked.\n"
    "- Do not contradict or restate the numeric score; explain what drove it.\n"
    "- Be specific and concise. No preamble, no restating the instructions.\n"
    "- You cannot take actions — you are recommending them to the agent."
)

LEAD_INSIGHT_PROMPT: PromptTemplate = PROMPTS.register(
    PromptTemplate(
        key="lead_insight",
        version=LEAD_INSIGHT_PROMPT_VERSION,
        system=LEAD_INSIGHT_SYSTEM,
        description="A grounded narrative summary and next steps for one lead.",
    )
)


__all__ = [
    "LEAD_INSIGHT_PROMPT",
    "LEAD_INSIGHT_PROMPT_VERSION",
    "LEAD_INSIGHT_SYSTEM",
]
