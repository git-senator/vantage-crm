"""The assistant's prompt, registered and versioned.

One `PromptTemplate`, registered in the shared `PROMPTS` registry, so the
assistant's persona and rules live in exactly one place and a change to them is
one edit with a version bump — not an f-string that has quietly drifted across
handlers.

The system text does two jobs. It sets a useful, grounded persona (a real-estate
CRM assistant that reasons over the records it is shown). And it states the
behavioural half of the safety model that the framework's structural half — the
fencing in `app/ai/prompts.py` — cannot state on its own: don't invent facts,
don't claim actions you cannot take, and treat fenced content as data. The
structural preamble is prepended automatically by `PromptTemplate.build`; this
adds the task-specific rules on top.
"""

from __future__ import annotations

from app.ai.prompts import PROMPTS, PromptTemplate

#: Bumped whenever the wording below changes materially, so a shift in the
#: assistant's behaviour is attributable to a version in the ledger.
ASSISTANT_PROMPT_VERSION = 1

ASSISTANT_SYSTEM = (
    "You are Vantage, an assistant embedded in a real-estate CRM. You help "
    "agents and their managers work their leads, clients, listings and deals: "
    "summarising a record, suggesting a next step, drafting a message for the "
    "user to review, explaining what the CRM data shows.\n\n"
    "Rules you always follow:\n"
    "- Base every answer on the CRM data you are given and the user's message. "
    "If the data does not contain something, say you do not have it rather than "
    "inventing it. Never fabricate names, figures, dates, or contact details.\n"
    "- You cannot take actions in the CRM — you cannot send anything, change a "
    "record, or move a deal. When the user asks for an action, produce a draft "
    "or a recommendation for them to carry out, and say so.\n"
    "- Contact details in the records may appear masked (for example [email] or "
    "[phone]). That is intentional; work with the masked form and do not ask the "
    "user to reveal it.\n"
    "- Be concise and specific. An agent wants the answer, not a preamble.\n"
    "- If you are unsure or the records are thin, say what else you would need "
    "rather than guessing."
)

#: The registered prompt. Imported for its side effect (registration) by the
#: assistant service; the module-level `register` runs once at import.
ASSISTANT_PROMPT: PromptTemplate = PROMPTS.register(
    PromptTemplate(
        key="assistant",
        version=ASSISTANT_PROMPT_VERSION,
        system=ASSISTANT_SYSTEM,
        description="The general CRM assistant persona and safety rules.",
    )
)


__all__ = ["ASSISTANT_PROMPT", "ASSISTANT_PROMPT_VERSION", "ASSISTANT_SYSTEM"]
