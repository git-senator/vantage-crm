"""Translate every listing that is missing a language, without waiting for the sweep.

The sweep runs on a timer and caps itself per pass, which is right for steady
state and wrong for the moment a catalogue lands: forty-one listings should not
trickle in over an hour while someone watches. This does the same work in the
same way, in one go, and reports what happened per listing.

    python -m scripts.backfill_translations            # everything outstanding
    python -m scripts.backfill_translations --limit 5  # a taste first

Run from the repository root so `.env` is found:

    PYTHONPATH=backend backend/.venv/Scripts/python.exe -m scripts.backfill_translations
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.models.property import Property
from app.services.ai.base import AIError, BudgetExceededError
from app.services.ai.service import AIService
from app.services.property_translation import (
    PropertyTranslationService,
    source_fingerprint,
)
from app.workers.context import (
    active_organization_ids,
    system_context,
    tenant_scope,
    unscoped_scope,
)

GRANTS = ("properties.view", "properties.manage", "ai.use")

#: Seconds between successful calls. Politeness, not correctness — the retry
#: below handles a refusal, this keeps most of them from happening.
PACE_SECONDS = 1.5

#: Waits after each failed attempt. A rate limit clears in seconds; a model
#: returning unusable JSON three times in a row will not be fixed by waiting
#: longer, so the ladder is short.
BACKOFF_SECONDS = (4, 12, 30)


async def _translate_with_backoff(
    service: PropertyTranslationService, listing: Property, locale: str
):  # type: ignore[no-untyped-def]
    """Attempt one translation, retrying a transient refusal.

    `BudgetExceededError` is deliberately not caught: it is not transient, and
    retrying it just burns through the remaining ceiling faster.
    """
    last: AIError | None = None
    for wait in (*BACKOFF_SECONDS, None):
        try:
            return await service.translate_locale(listing, locale)
        except BudgetExceededError:
            raise
        except AIError as exc:
            last = exc
            if wait is None:
                break
            await asyncio.sleep(wait)
    raise last if last else AIError("Translation failed for an unknown reason.")


async def run(limit: int | None) -> int:
    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    failures = 0
    for organization in organizations:
        auth = system_context(organization, *GRANTS)

        async with tenant_scope(organization) as session:
            rows = await session.execute(
                select(Property).where(Property.deleted_at.is_(None))
            )
            listings = list(rows.scalars().all())

        if limit is not None:
            listings = listings[:limit]

        print(f"organization {organization}: {len(listings)} listings")

        for index, listing in enumerate(listings, start=1):
            # A session per listing, committed on its own: a model failure on
            # listing thirty must not roll back the twenty-nine before it.
            async with tenant_scope(organization) as session:
                fresh = await session.get(Property, listing.id)
                if fresh is None:
                    continue

                service = PropertyTranslationService(
                    session, ai=AIService(session, auth)
                )
                await service.mark_stale(fresh)
                pending = await service.pending_locales(fresh)
                if not pending:
                    print(f"  [{index:>3}] {fresh.title[:44]:46} up to date")
                    await session.commit()
                    continue

                fingerprint = source_fingerprint(fresh)
                done: list[str] = []
                for locale in pending:
                    try:
                        result = await _translate_with_backoff(
                            service, fresh, locale
                        )
                    except BudgetExceededError:
                        print("  BUDGET EXHAUSTED — stopping", file=sys.stderr)
                        await session.commit()
                        return failures + 1
                    except AIError as exc:
                        print(f"       {locale}: FAILED — {exc}", file=sys.stderr)
                        failures += 1
                        continue
                    await service.store(
                        fresh, locale, result, fingerprint=fingerprint
                    )
                    done.append(locale)
                    # Free-tier Groq rate-limits a tight loop within a few
                    # calls. Backing off after a 429 is not enough on its own —
                    # eighty-two requests fired as fast as they complete will
                    # spend most of their time being refused.
                    await asyncio.sleep(PACE_SECONDS)

                await session.commit()
                print(
                    f"  [{index:>3}] {fresh.title[:44]:46} -> {','.join(done) or 'none'}"
                )

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None, help="only the first N listings"
    )
    args = parser.parse_args()

    failures = asyncio.run(run(args.limit))
    if failures:
        print(f"\n{failures} translation(s) failed — rerun to retry them.")
        raise SystemExit(1)
    print("\nall listings translated.")


if __name__ == "__main__":
    main()
