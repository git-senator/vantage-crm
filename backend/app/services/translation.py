"""Seamless inbox translation.

The two-sided translated inbox needs one thing from the model: given a message,
produce it in each team language so whoever opens the thread reads it in their
own. Inbound gets this from the orchestrator, which is already an AI pipeline;
outbound — a manager typing a reply — has no orchestrator in the loop, so the
CRM does it here at send time.

**Best-effort, never a gate.** Translation is an enhancement to a message, not a
precondition for sending one. It deliberately does *not* go through `AIService`:
that path requires `ai.use` and draws down the tenant's AI budget, and coupling
"can this person send a reply" to "is there AI budget left" is the wrong
dependency — a message must still send when translation is off, unconfigured, or
failing. So this calls the provider directly and swallows every failure into
"no translations", leaving the original body to stand on its own.

The provider and key are the same ones the assistant uses (`AI_*` settings); the
egress is the same customer text the assistant already sends. A production
deployment that wants translation egress on the AI ledger can route this through
`AIService` later — the shape of what is stored does not change.
"""

from __future__ import annotations

import json

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.ai.base import ChatMessage, CompletionRequest
from app.services.ai.openai_compatible import OpenAICompatibleProvider

logger = get_logger(__name__)

#: The languages the inbox renders into. Translating outside this set is wasted
#: egress — the UI has no locale to show the result under.
TEAM_LANGS: tuple[str, ...] = ("ru", "pt", "en")

_SYSTEM = (
    "You are the translation engine of a real-estate CRM inbox. Translate the "
    "user's message into Russian, Brazilian Portuguese and English, and detect "
    "its source language. Preserve every detail — budget figures, locations, "
    "dates, property types — and keep the tone natural, as a real estate agent "
    "would write it. Return ONLY a JSON object with keys: lang (two-letter code "
    "ru, pt or en), ru (string), pt (string), en (string). No prose, no code "
    "fence."
)


def _extract_json(text: str) -> dict[str, object]:
    """Pull the JSON object out of a model reply.

    Reasoning models occasionally wrap the object in a code fence or a line of
    preamble despite the instruction not to; slicing to the outermost braces is
    cheaper and more forgiving than refusing anything that is not pristine.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class TranslationService:
    """Translate a message body into the team languages. Never raises."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        settings = self._settings
        return (
            settings.AI_ENABLED
            and settings.AI_PROVIDER == "openai_compatible"
            and bool(settings.AI_API_KEY.get_secret_value())
        )

    async def translate(
        self, body: str, *, source_lang: str | None = None
    ) -> tuple[str | None, dict[str, str]]:
        """Return `(detected_lang, {locale: text})` for the team languages.

        On anything short of a clean success — translation disabled, the model
        unreachable, a reply that will not parse — returns the source language
        (if known) and an empty map. The caller stores the original body either
        way; an empty map simply means every viewer sees that original, which is
        the honest degraded state, not a broken one.
        """
        text = body.strip()
        if not text or not self.enabled:
            return source_lang, {}

        provider = OpenAICompatibleProvider(self._settings)
        request = CompletionRequest(
            system=_SYSTEM,
            messages=[ChatMessage(role="user", content=text)],
            model=self._settings.AI_MODEL,
            max_tokens=self._settings.AI_MAX_OUTPUT_TOKENS or 1500,
            temperature=0.0,
            metadata={"feature": "inbox_translation"},
        )
        try:
            result = await provider.complete(request)
        except Exception:  # noqa: BLE001 - best-effort; a failure must not block send
            logger.warning("translation_failed", exc_info=True)
            return source_lang, {}

        data = _extract_json(result.text)
        translations = {
            locale: value
            for locale in TEAM_LANGS
            if isinstance(value := data.get(locale), str) and value.strip()
        }
        detected = data.get("lang")
        lang = detected if isinstance(detected, str) and detected else source_lang
        return lang, translations
