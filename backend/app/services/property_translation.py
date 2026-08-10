"""PropertyTranslationService — a listing's text in every language the UI ships.

Three jobs, deliberately separable:

  * decide *what* needs translating (`pending_locales`) — pure, given a listing
    and its existing rows, and therefore testable without a model;
  * produce a translation (`translate_locale`) — the one part that calls out;
  * write it down (`store`) — an upsert that never overwrites human work.

The split matters because the middle step is the flaky one. Groq answers 429
under load and models occasionally return prose where JSON was asked for, so
the decision about what to do must not be entangled with the attempt to do it:
a failed translation leaves the listing exactly as it was, still marked as
needing one, and the sweep tries again.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import ContentBlock
from app.ai.translation_prompts import (
    LOCALE_NAMES,
    PROPERTY_TRANSLATION_PROMPT,
)
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.property import Property
from app.models.property_translation import LOCALES, PropertyTranslation
from app.services.ai.base import AIError
from app.services.ai.service import AIService

logger = get_logger(__name__)

#: Feature tag for AI egress accounting, so translation spend is visible
#: separately from the assistant's in the same ledger everything else uses.
TRANSLATION_FEATURE = "property_translation"


class TranslationResult:
    """One language's worth of translated listing text."""

    __slots__ = ("description", "features", "title")

    def __init__(
        self, title: str, description: str | None, features: list[str]
    ) -> None:
        self.title = title
        self.description = description
        self.features = features


def source_fingerprint(listing: Property) -> str:
    """A stable hash of exactly the text that gets translated.

    Only the translated fields go in. Bumping the price or changing the cover
    photo must not invalidate a perfectly good Portuguese description — that
    would burn tokens on every edit and reset human corrections for no reason.
    """
    payload = json.dumps(
        {
            "title": listing.title,
            "description": listing.description or "",
            "features": list(listing.features or []),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def target_locales(listing: Property) -> list[str]:
    """Every locale this listing needs, which is all of them but its own."""
    return [locale for locale in LOCALES if locale != listing.source_locale]


def parse_translation(raw: str, *, expected_features: int) -> TranslationResult:
    """Read the model's reply, refusing anything that is not usable.

    Models wrap JSON in prose or markdown fences often enough that stripping
    them is expected behaviour rather than a workaround. Everything past that
    is a hard failure: a translation with the wrong number of feature labels
    has dropped or invented one, and quietly storing it would put a listing
    into the UI with a feature the agent never wrote.
    """
    text = raw.strip()
    if text.startswith("```"):
        # ```json … ``` — take what is between the first and last fence.
        text = text.split("```")[1] if text.count("```") >= 2 else text
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise AIError("Translation reply contained no JSON object.")

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AIError(f"Translation reply was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise AIError("Translation reply was not a JSON object.")

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise AIError("Translation reply has no usable title.")

    description = data.get("description")
    if description is not None and not isinstance(description, str):
        raise AIError("Translation reply has a non-string description.")

    features = data.get("features") or []
    if not isinstance(features, list) or not all(
        isinstance(item, str) for item in features
    ):
        raise AIError("Translation reply has a malformed features list.")
    if len(features) != expected_features:
        raise AIError(
            f"Translation returned {len(features)} features, "
            f"expected {expected_features}."
        )

    return TranslationResult(
        # The column is String(200); a model that pads the title must not take
        # the whole write down with it.
        title=title.strip()[:200],
        description=(description.strip() or None) if description else None,
        features=[item.strip()[:60] for item in features],
    )


class PropertyTranslationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        ai: AIService,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.ai = ai
        self.settings = settings or get_settings()

    # ------------------------------------------------------------ deciding

    async def pending_locales(self, listing: Property) -> list[str]:
        """Which languages are missing or out of date for this listing.

        A translation is pending when there is none, or when the source has
        moved on since it was made — except where a person edited it. Human
        text is never regenerated; it is flagged stale for someone to look at,
        which `mark_stale` does.
        """
        fingerprint = source_fingerprint(listing)
        existing = {
            row.locale: row
            for row in await self._rows_for(listing.id)
        }

        pending: list[str] = []
        for locale in target_locales(listing):
            row = existing.get(locale)
            if row is None:
                pending.append(locale)
            elif row.source_hash != fingerprint and row.is_machine:
                pending.append(locale)
        return pending

    async def mark_stale(self, listing: Property) -> int:
        """Flag human-edited translations whose source has since changed.

        Returns how many were flagged. This is the other half of not
        overwriting people: their text stays, and the interface can say that
        the listing has moved on underneath it.
        """
        fingerprint = source_fingerprint(listing)
        flagged = 0
        for row in await self._rows_for(listing.id):
            if row.is_machine or row.source_hash == fingerprint or row.is_stale:
                continue
            row.is_stale = True
            flagged += 1
        return flagged

    # --------------------------------------------------------- translating

    async def translate_locale(self, listing: Property, locale: str) -> TranslationResult:
        """Translate one listing into one language. Calls the model."""
        if locale not in LOCALE_NAMES:
            raise AIError(f"Unknown locale '{locale}'.")

        source_name = LOCALE_NAMES.get(listing.source_locale, listing.source_locale)
        features = list(listing.features or [])

        blocks: list[ContentBlock] = [
            ContentBlock(
                text=(
                    f"Translate this property listing from {source_name} into "
                    f"{LOCALE_NAMES[locale]}."
                ),
                trusted=True,
            ),
            # Fenced and escaped: this text came off a public website and is
            # not ours. The prompt tells the model to translate instructions
            # rather than obey them; the fence is what makes that instruction
            # about something the model can actually identify.
            ContentBlock(
                text=json.dumps(
                    {
                        "title": listing.title,
                        "description": listing.description or "",
                        "features": features,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                label="listing",
            ),
        ]

        request = PROPERTY_TRANSLATION_PROMPT.build(
            blocks,
            model=self.settings.AI_MODEL,
            max_tokens=self.settings.AI_MAX_OUTPUT_TOKENS,
            # Translation wants the obvious word, not an interesting one.
            temperature=0.0,
            metadata={"property": str(listing.id), "locale": locale},
        )
        completion = await self.ai.complete(request, feature=TRANSLATION_FEATURE)
        return parse_translation(completion.text, expected_features=len(features))

    # ------------------------------------------------------------ storing

    async def store(
        self,
        listing: Property,
        locale: str,
        result: TranslationResult,
        *,
        fingerprint: str | None = None,
    ) -> None:
        """Upsert one translation, leaving human-edited text alone.

        The `WHERE is_machine` on the update is the safety catch, enforced by
        the database rather than by remembering to check first: two jobs racing
        for the same listing cannot have one of them clobber an edit that
        landed between the read and the write.
        """
        now = datetime.now(UTC)
        statement = (
            pg_insert(PropertyTranslation)
            .values(
                organization_id=listing.organization_id,
                property_id=listing.id,
                locale=locale,
                title=result.title,
                description=result.description,
                features=result.features,
                is_machine=True,
                is_stale=False,
                source_hash=fingerprint or source_fingerprint(listing),
                translated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_property_translation",
                set_={
                    "title": result.title,
                    "description": result.description,
                    "features": result.features,
                    "source_hash": fingerprint or source_fingerprint(listing),
                    "translated_at": now,
                    "is_stale": False,
                    "updated_at": now,
                },
                where=PropertyTranslation.is_machine.is_(True),
            )
        )
        await self.session.execute(statement)

    # -------------------------------------------------------------- reading

    async def for_property(self, property_id: UUID) -> dict[str, PropertyTranslation]:
        """Every stored translation for one listing, keyed by locale."""
        return {row.locale: row for row in await self._rows_for(property_id)}

    async def _rows_for(self, property_id: UUID) -> list[PropertyTranslation]:
        result = await self.session.execute(
            select(PropertyTranslation).where(
                PropertyTranslation.property_id == property_id
            )
        )
        return list(result.scalars().all())
