"""Listing-translation jobs: translate one listing, and find the ones missed.

The same two-part shape as document scanning, for the same reason. `enqueue` is
best-effort — Redis restarts, the worker redeploys mid-job, Groq answers 429 —
so the queue is not allowed to be the record of what needs doing. The database
is: a listing with no row in `property_translations` for a locale *is* the
outstanding work, and `sweep_property_translations` re-finds it.

Without the sweep, a failed translation is silent, and a silently untranslated
listing looks exactly like a translated one until a client is reading it.

Both jobs bind the tenant before touching data. A sweep written without that
runs cleanly and does nothing, because RLS returns no rows to an unscoped
session — a failure indistinguishable from "there was nothing to do".
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.property import Property
from app.models.property_translation import PropertyTranslation
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
from app.workers.queue import JobName, enqueue
from app.workers.runner import job

logger = get_logger(__name__)

#: Narrow on purpose: a translator has no business reading a deal.
_TRANSLATION_GRANTS = ("properties.view", "properties.manage", "ai.use")

#: How many listings one sweep will queue per tenant. A first run after an
#: import has forty-one waiting and a bulk import could have thousands; the cap
#: keeps a single sweep from turning into an unbounded spend.
_SWEEP_LIMIT = 100


@job(organization_arg=1, max_tries=3)
async def translate_property(
    ctx: dict[str, Any], property_id: str, organization_id: str
) -> str:
    """Translate one listing into every language it is missing.

    Returns a short summary so a run reads meaningfully in the job log.

    Each locale is attempted independently: a model that returns unusable JSON
    for Portuguese should not cost the listing its English. A locale that fails
    is simply left pending, and the sweep comes back for it.
    """
    organization = UUID(organization_id)
    listing_id = UUID(property_id)
    settings = get_settings()

    if not settings.AI_ENABLED:
        return "ai disabled"

    auth = system_context(organization, *_TRANSLATION_GRANTS)

    async with tenant_scope(organization) as session:
        listing = await session.get(Property, listing_id)
        if listing is None or listing.deleted_at is not None:
            return "listing gone"

        service = PropertyTranslationService(
            session, ai=AIService(session, auth, settings=settings), settings=settings
        )

        # Human-edited translations whose source moved on are flagged, never
        # regenerated. This runs first so the flag is set even if every
        # translation below fails.
        flagged = await service.mark_stale(listing)

        pending = await service.pending_locales(listing)
        if not pending and not flagged:
            return "up to date"

        fingerprint = source_fingerprint(listing)
        done: list[str] = []
        failed: list[str] = []

        for locale in pending:
            try:
                result = await service.translate_locale(listing, locale)
            except BudgetExceededError:
                # The tenant is out of AI budget. Stopping is right: the
                # remaining locales would fail identically, and hammering the
                # ceiling turns one refusal into a dozen.
                logger.warning(
                    "translation_budget_exhausted",
                    extra={"property": property_id, "organization": organization_id},
                )
                break
            except AIError as exc:
                # Left pending on purpose — the sweep retries it. Raising here
                # would retry the locales that already succeeded too.
                logger.warning(
                    "translation_failed",
                    extra={
                        "property": property_id,
                        "locale": locale,
                        "error": str(exc),
                    },
                )
                failed.append(locale)
                continue

            await service.store(listing, locale, result, fingerprint=fingerprint)
            done.append(locale)

        await session.commit()

    return (
        f"translated={','.join(done) or '-'} "
        f"failed={','.join(failed) or '-'} stale_flagged={flagged}"
    )


@job(max_tries=2)
async def sweep_property_translations(ctx: dict[str, Any]) -> int:
    """Queue every listing whose translations are missing or out of date.

    Returns how many were queued. This is the net under `enqueue`: the query
    below asks the database what is outstanding rather than trusting that a
    message was ever delivered.
    """
    settings = get_settings()
    if not settings.AI_ENABLED:
        return 0

    async with unscoped_scope() as session:
        organizations = await active_organization_ids(session)

    queued = 0
    for organization in organizations:
        async with tenant_scope(organization) as session:
            listings = await _listings_needing_translation(session)

        for listing_id in listings:
            # A derived job id, so a listing already queued is not queued
            # twice — the sweep runs on a timer and does not know what the
            # last one did.
            if await enqueue(
                JobName.TRANSLATE_PROPERTY,
                str(listing_id),
                str(organization),
                job_id=f"translate:{listing_id}",
            ):
                queued += 1

    if queued:
        logger.info("property_translations_swept", extra={"queued": queued})
    return queued


async def _listings_needing_translation(session: Any) -> list[UUID]:
    """Listings with fewer usable translations than they should have.

    "Usable" excludes stale rows, so a listing whose source changed under a
    human-edited translation comes back around — the flag is what surfaces it
    to the agent, and re-queueing costs one no-op job rather than a missed
    correction.

    Machine rows whose `source_hash` has diverged are handled by the job
    itself; this only has to find candidates, and a listing that turns out to
    be up to date returns "up to date" for the price of one query.
    """
    # Two locales are expected for every listing: three shipped, minus its own.
    expected = 2
    statement = (
        select(Property.id)
        .outerjoin(
            PropertyTranslation,
            (PropertyTranslation.property_id == Property.id)
            & (PropertyTranslation.is_stale.is_(False)),
        )
        .where(Property.deleted_at.is_(None))
        .group_by(Property.id)
        .having(
            # Counting the translation id rather than * matters: over an outer
            # join, count(*) counts the listing row itself, so a listing with
            # no translations at all would score one instead of zero and never
            # be picked up.
            func.count(PropertyTranslation.id)
            < expected
        )
        .limit(_SWEEP_LIMIT)
    )
    rows = await session.execute(statement)
    return [row[0] for row in rows.all()]
