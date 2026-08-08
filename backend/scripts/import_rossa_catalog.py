"""Import the Rossa Group public catalogue into the CRM.

The brokerage's website (rossagroupbrazil.com) already holds the whole book of
listings, photography included, in a single JSON blob the page ships to the
browser: ``window.RGB_CATALOG``. That is the source of truth this reads. There
is no scraping of rendered markup, so a cosmetic redesign of the site does not
break the import — only a change to that structure would, and then loudly.

    python backend/scripts/import_rossa_catalog.py --agent-email you@example.com

Run it from the repository root, where ``.env`` lives. Against the local stack
the database and object storage are published on loopback, so point the two
hosts at it:

    POSTGRES_HOST=127.0.0.1 S3_ENDPOINT_URL=http://127.0.0.1:9000 \
        backend/.venv/Scripts/python.exe backend/scripts/import_rossa_catalog.py \
        --agent-email qa@vantagecrm.io

**Re-running is safe.** A listing is matched by its site slug, kept in
``custom_fields.source_slug``, and updated in place — so the second run
refreshes prices and descriptions instead of creating forty-one duplicates.
Photos are skipped for a listing that already has them unless
``--refresh-photos`` says otherwise, because re-uploading a hundred megabytes to
re-create identical objects is waste, not idempotence.

Photos are downloaded to a local cache first, outside of any transaction. A
listing's database work then happens in one short transaction: holding one open
across a hundred megabytes of network transfer is how an import ends up
deadlocked against ordinary traffic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

# The script lives beside `app`, not inside it — it is a one-off import tool,
# not shipped runtime code, and the container image deliberately does not carry
# it. That means the package root has to be put on the path by hand.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.session import session_scope
from app.models.attachment import Attachment
from app.models.property import Property
from app.services.storage import (
    ObjectNotFoundError,
    StorageError,
    build_object_storage,
)
from app.services.storage.keys import build_storage_key

CATALOG_URL = "https://rossagroupbrazil.com/rus"

#: The site's own region keys, mapped to a real city and federal unit. Taken
#: from the region rather than parsed out of the display label: several labels
#: read "Florianópolis · Санта-Катарина", where the part after the separator is
#: the *state*, and a naive split files the listing in a city that is a state.
REGIONS: dict[str, tuple[str, str]] = {
    "florianopolis": ("Florianópolis", "SC"),
    "balneario-camboriu": ("Balneário Camboriú", "SC"),
    "rio-de-janeiro": ("Rio de Janeiro", "RJ"),
    "sao-paulo": ("São Paulo", "SP"),
}

#: The site's categories against the CRM's fixed vocabulary. The CRM's list is
#: North-American in origin: a Brazilian apartment in a tower is a `condo`, and
#: a standalone villa is a `single_family`. Neither is a perfect word, but both
#: are the right *kind* of thing, which is what the filters act on.
PROPERTY_TYPES: dict[str, str] = {
    "apartment": "condo",
    "villa": "single_family",
    "house": "single_family",
    "commercial": "commercial",
    "land": "land",
}

#: 1 m² in square feet. The column is `square_feet`, so the number stored in it
#: has to actually be square feet — the true area in m² is kept alongside in
#: `custom_fields.area_m2`, since that is the unit every document about these
#: properties is written in.
SQFT_PER_M2 = 10.7639

_NUMERALS = {
    "одна": 1, "одно": 1, "две": 2, "два": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
}
_BEDROOM_DIGITS = re.compile(
    r"(\d+)\s*(?:\+\s*\d+\s*)?(?:спальн|сьют|сюит|suíte|suite|quarto|dorm)",
    re.IGNORECASE,
)
_BEDROOM_WORDS = re.compile(
    r"\b(" + "|".join(_NUMERALS) + r")\s+(?:спальн|сьют|сюит)", re.IGNORECASE
)
_AREA = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:м²|m²|м2|m2)", re.IGNORECASE)


# --------------------------------------------------------------- extraction


def extract_catalog(html: str) -> tuple[str, list[dict[str, Any]]]:
    """Pull ``window.RGB_CATALOG`` out of the page.

    Returns the CDN base and the raw items. Brace-balanced rather than
    regex-matched: the blob contains braces inside strings, and a lazy `.*?`
    would stop at the first one.
    """
    marker = "window.RGB_CATALOG"
    start = html.find(marker)
    if start < 0:
        raise SystemExit(
            "window.RGB_CATALOG not found — the site's catalogue format changed."
        )
    brace = html.find("{", start)
    depth = 0
    end = -1
    for index in range(brace, len(html)):
        char = html[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end < 0:
        raise SystemExit("window.RGB_CATALOG is truncated — nothing imported.")

    payload = json.loads(html[brace : end + 1])
    return payload.get("base", ""), payload.get("items", [])


def _first_number(pattern: re.Pattern[str], haystack: str) -> float | None:
    match = pattern.search(haystack)
    if match is None:
        return None
    return float(match.group(1).replace(",", "."))


def _bedrooms(text_blob: str) -> int | None:
    """Smallest bedroom count mentioned.

    A development advertises "1, 2 and 3 bedrooms" — the smallest is the one
    the "from" price belongs to, and it is also the only answer that keeps the
    listing visible to someone filtering for at least one bedroom.
    """
    counts = [int(match) for match in _BEDROOM_DIGITS.findall(text_blob)]
    counts += [_NUMERALS[word.lower()] for word in _BEDROOM_WORDS.findall(text_blob)]
    sane = [count for count in counts if 0 < count <= 20]
    return min(sane) if sane else None


def _area_m2(text_blob: str) -> float | None:
    areas = [
        float(value.replace(",", "."))
        for value in _AREA.findall(text_blob)
    ]
    sane = [area for area in areas if 10 <= area <= 100_000]
    return min(sane) if sane else None


@dataclass
class Listing:
    """One catalogue entry, already in the CRM's terms."""

    slug: str
    title: str
    property_type: str
    address_line1: str
    city: str
    state: str
    price: Decimal | None
    bedrooms: int | None
    square_feet: int | None
    description: str
    features: list[str]
    custom_fields: dict[str, Any]
    photo_urls: list[str] = field(default_factory=list)
    cover_index: int = 0


def build_listing(raw: dict[str, Any], base: str) -> Listing:
    location = str(raw.get("location") or "").strip()
    region_key = (raw.get("region") or ["florianopolis"])[0]
    city, state = REGIONS.get(region_key, ("Florianópolis", "SC"))

    # The part before the separator is the neighbourhood — the closest thing
    # the catalogue has to a street address, and what an agent recognises.
    neighbourhood = location.split("·")[0].strip() or city

    facts = raw.get("facts") or []
    fact_text = " ".join(
        f"{row.get('label') or ''} {row.get('value') or ''}"
        for group in facts
        for row in (group.get("rows") or [])
    )
    lead = str(raw.get("lead") or "").strip()
    searchable = f"{raw.get('title', '')} {lead} {fact_text}"

    area_m2 = _area_m2(searchable)
    price_block = raw.get("price") or {}
    usd = price_block.get("usd")
    per_month = bool(raw.get("perMonth"))

    return Listing(
        slug=str(raw["slug"]),
        title=str(raw.get("title") or raw["slug"])[:200],
        property_type=PROPERTY_TYPES.get((raw.get("type") or ["apartment"])[0], "condo"),
        address_line1=neighbourhood[:200],
        city=city,
        state=state,
        price=Decimal(str(usd)) if usd else None,
        bedrooms=_bedrooms(searchable),
        square_feet=round(area_m2 * SQFT_PER_M2) if area_m2 else None,
        description=_describe(raw, lead, facts, per_month, usd),
        features=_features(raw, facts),
        custom_fields={
            "source": "rossagroupbrazil.com",
            "source_slug": raw["slug"],
            "source_url": f"{CATALOG_URL}#catalog",
            "location_label": location,
            "badge": raw.get("badge"),
            "region": region_key,
            "deal": (raw.get("deal") or ["buy"])[0],
            "per_month": per_month,
            "price_from": bool(price_block.get("from")),
            "price_on_request": bool(price_block.get("onRequest")),
            # The honest area, in the unit the paperwork uses.
            "area_m2": area_m2,
            "facts": facts,
            "note": raw.get("note"),
        },
        photo_urls=[f"{base}{path}" for path in (raw.get("photos") or [])],
        cover_index=int(raw.get("cover") or 0),
    )


def _describe(
    raw: dict[str, Any],
    lead: str,
    facts: list[dict[str, Any]],
    per_month: bool,
    usd: Any,
) -> str:
    """Flatten the site's structured facts into readable prose.

    The structure is kept verbatim in `custom_fields.facts` as well — this copy
    exists so the text is searchable and so an agent reading the record sees
    everything the customer saw, in the same order.
    """
    parts: list[str] = []
    if lead:
        parts.append(lead)
    if per_month and usd:
        parts.append(f"Аренда: ${int(usd):,}".replace(",", " ") + " в месяц.")

    for group in facts:
        rows = group.get("rows") or []
        if not rows:
            continue
        parts.append(f"\n{group.get('group') or 'Характеристики'}:")
        for row in rows:
            label = (row.get("label") or "").strip()
            value = (row.get("value") or "").strip()
            if label and value:
                parts.append(f"• {label} — {value}")
            elif label or value:
                parts.append(f"• {label or value}")

    note = (raw.get("note") or "").strip()
    if note:
        parts.append(f"\n{note}")
    return "\n".join(parts)[:20_000]


def _features(raw: dict[str, Any], facts: list[dict[str, Any]]) -> list[str]:
    """Short, filterable tags. The column caps at 40 entries of 60 characters."""
    tags: list[str] = []
    badge = (raw.get("badge") or "").strip()
    if badge:
        tags.append(badge)
    tags.append("Аренда" if (raw.get("deal") or ["buy"])[0] == "rent" else "Продажа")
    for group in facts:
        for row in group.get("rows") or []:
            label = (row.get("label") or "").strip()
            if label and len(label) <= 60 and label not in tags:
                tags.append(label)
    return tags[:40]


# ------------------------------------------------------------------ photos


async def cache_photos(
    listings: list[Listing], cache_dir: Path, concurrency: int
) -> dict[str, Path]:
    """Fetch every photo to disk once, before any transaction opens.

    Already-cached files are left alone, so an interrupted run resumes instead
    of starting the download over.
    """
    # Blocking filesystem calls in an async function, deliberately: these are
    # local metadata operations and small writes on a one-off script, and an
    # async filesystem layer would buy nothing but a dependency.
    cache_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    wanted: dict[str, Path] = {}
    for listing in listings:
        for index, url in enumerate(listing.photo_urls):
            wanted[url] = cache_dir / f"{listing.slug}-{index:03d}.webp"

    missing = {url: path for url, path in wanted.items() if not path.exists()}
    if not missing:
        print(f"фото: все {len(wanted)} уже в кэше")
        return wanted

    print(f"фото: качаю {len(missing)} из {len(wanted)} (остальное в кэше)")
    semaphore = asyncio.Semaphore(concurrency)
    failures: list[str] = []

    async with httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": "VantageCRM-Import/1.0"},
    ) as client:

        async def fetch(url: str, path: Path) -> None:
            async with semaphore:
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    failures.append(f"{url}: {exc}")
                    return
                # Written via a temporary name so an interrupted write cannot
                # leave a truncated file that the next run trusts as cached.
                temporary = path.with_suffix(".part")
                temporary.write_bytes(response.content)
                temporary.replace(path)

        tasks = [fetch(url, path) for url, path in missing.items()]
        for done, coroutine in enumerate(asyncio.as_completed(tasks), start=1):
            await coroutine
            if done % 50 == 0 or done == len(missing):
                print(f"  {done}/{len(missing)}")

    for failure in failures:
        print(f"  ! не скачалось — {failure}")
    return {url: path for url, path in wanted.items() if path.exists()}


# --------------------------------------------------------------- importing


async def resolve_actor(email: str) -> tuple[UUID, UUID]:
    """The importing agent and their tenant.

    Uses the same `SECURITY DEFINER` lookup the login flow uses: `users` is
    RLS'd on the tenant, so an unbound session cannot read it — and the tenant
    is precisely what we are trying to discover.
    """
    async with session_scope() as session:
        row = (
            await session.execute(
                text("SELECT user_id, organization_id FROM lookup_login_identity(:e)"),
                {"e": email},
            )
        ).first()
    if row is None:
        raise SystemExit(f"Пользователь {email} не найден.")
    return row[0], row[1]


async def import_listing(
    listing: Listing,
    *,
    organization_id: UUID,
    actor_id: UUID,
    photos: dict[str, Path],
    storage: Any,
    refresh_photos: bool,
) -> str:
    settings = get_settings()

    async with session_scope(organization_id, actor_id) as session:
        existing = (
            await session.execute(
                select(Property)
                .where(Property.deleted_at.is_(None))
                .where(
                    Property.custom_fields["source_slug"].astext == listing.slug
                )
            )
        ).scalars().first()

        row = existing or Property(
            organization_id=organization_id,
            listing_agent_id=actor_id,
            created_by=actor_id,
            status="active",
            currency="USD",
            country="BR",
            # No CEP in the catalogue and none invented: a fabricated postcode
            # would look authoritative in an export or a contract.
            postal_code="-",
        )
        row.title = listing.title
        row.property_type = listing.property_type
        row.address_line1 = listing.address_line1
        row.city = listing.city
        row.state = listing.state
        row.price = listing.price
        row.bedrooms = listing.bedrooms
        row.square_feet = listing.square_feet
        row.description = listing.description
        row.features = listing.features
        row.custom_fields = listing.custom_fields
        row.updated_by = actor_id
        if existing is None:
            session.add(row)
        await session.flush()

        current = (
            await session.execute(
                select(Attachment)
                .where(Attachment.entity_type == "property")
                .where(Attachment.entity_id == row.id)
                .where(Attachment.deleted_at.is_(None))
            )
        ).scalars().all()

        if current and not refresh_photos:
            return f"обновлён (фото уже есть: {len(current)})"

        # Replacing the set: drop the old rows and their bytes first, so a
        # refresh does not leave the gallery holding two of every photo.
        for stale in current:
            if stale.storage_key:
                # An object that is already gone is the outcome we wanted.
                with suppress(StorageError, ObjectNotFoundError):
                    await storage.delete(stale.storage_key)
            await session.delete(stale)
        if current:
            row.cover_attachment_id = None
            await session.flush()

        stored: list[Attachment] = []
        for index, url in enumerate(listing.photo_urls):
            path = photos.get(url)
            if path is None:
                continue
            data = path.read_bytes()
            if len(data) > settings.MAX_UPLOAD_BYTES:
                print(f"    ! {path.name} больше лимита, пропускаю")
                continue

            # Registered first, published last — the same order the real upload
            # takes, and the order the table's own constraint requires: nothing
            # may be `available` before it has a storage key, and the key needs
            # the row's id.
            attachment = Attachment(
                organization_id=organization_id,
                uploaded_by=actor_id,
                entity_type="property",
                entity_id=row.id,
                filename=f"{listing.slug}-{index:03d}.webp",
                content_type="image/webp",
                size_bytes=len(data),
                storage_backend=storage.name,
                status="pending_upload",
                # Not "clean": nothing scanned these. `skipped` is the honest
                # value and is what the platform already uses for files that
                # bypass the scanner.
                scan_status="skipped",
                sort_order=index,
            )
            session.add(attachment)
            await session.flush()

            attachment.storage_key = build_storage_key(
                organization_id=organization_id,
                entity_type="property",
                entity_id=row.id,
                attachment_id=attachment.id,
                filename=attachment.filename,
            )
            await storage.write(
                attachment.storage_key, data, content_type="image/webp"
            )
            attachment.status = "available"
            attachment.available_at = datetime.now(UTC)
            await session.flush()
            stored.append(attachment)

        if stored:
            cover = stored[min(listing.cover_index, len(stored) - 1)]
            row.cover_attachment_id = cover.id
        await session.flush()

    verb = "обновлён" if existing is not None else "создан"
    return f"{verb}, фото: {len(stored)}"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-email", required=True)
    parser.add_argument("--url", default=CATALOG_URL)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache/rossa-photos"),
        help="куда складывать скачанные фото между запусками",
    )
    parser.add_argument("--limit", type=int, default=0, help="взять только N объектов")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--refresh-photos",
        action="store_true",
        help="перезалить фото даже там, где они уже есть",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="разобрать каталог и ничего не писать"
    )
    args = parser.parse_args()

    print(f"читаю {args.url}")
    async with httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": "VantageCRM-Import/1.0"},
    ) as client:
        response = await client.get(args.url)
        response.raise_for_status()

    base, items = extract_catalog(response.text)
    listings = [build_listing(item, base) for item in items]
    if args.limit:
        listings = listings[: args.limit]
    total_photos = sum(len(listing.photo_urls) for listing in listings)
    print(f"объектов: {len(listings)}, фото: {total_photos}")

    if args.dry_run:
        for listing in listings:
            print(
                f"  {listing.slug:38} {listing.title[:32]:34} "
                f"{listing.city:20} {listing.property_type:14} "
                f"price={listing.price} beds={listing.bedrooms} "
                f"m2={listing.custom_fields['area_m2']} photos={len(listing.photo_urls)}"
            )
        return

    actor_id, organization_id = await resolve_actor(args.agent_email)
    print(f"агент: {args.agent_email}  организация: {organization_id}")

    photos = await cache_photos(listings, args.cache_dir, args.concurrency)
    storage = build_object_storage(get_settings())

    for number, listing in enumerate(listings, start=1):
        result = await import_listing(
            listing,
            organization_id=organization_id,
            actor_id=actor_id,
            photos=photos,
            storage=storage,
            refresh_photos=args.refresh_photos,
        )
        print(f"[{number:2}/{len(listings)}] {listing.slug:38} {result}")

    print("готово")


if __name__ == "__main__":
    asyncio.run(main())
