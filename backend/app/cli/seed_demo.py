"""Generate a large, realistic demo dataset for benchmarking and local testing.

    python -m app.cli.seed_demo --org-slug rossa-crm --scale 1.0

At `--scale 1.0` this produces roughly a quarter-million rows — ~100k leads on
their own — which is the population Phase 2's list-latency exit criterion is
measured against (p95 < 200 ms). Dial it down for a quick local run
(`--scale 0.05`) or up for a stress test.

Design notes that matter:

  * **Ids are generated up front (UUIDv7), not on flush.** Deals reference
    clients, notes reference anything — knowing every id before insert lets the
    cross-references be pure in-memory random picks with no round-trips.
  * **One transaction per batch, each re-binding the tenant context.** RLS is
    enforced by `SET LOCAL app.current_org`, which is transaction-scoped; a
    single giant transaction would hold locks and WAL for the whole run, so
    each batch commits independently and re-binds the context.
  * **Ownership is spread across many agents.** Scope tests and the dashboard's
    per-agent numbers are only meaningful if records are not all owned by one
    person, so the generator seeds a pool of agents and distributes across them.

Not wired into any HTTP surface — it writes bulk data under a tenant's context
and is a developer tool, exactly like `bootstrap`.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from uuid6 import uuid7

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.session import get_session_factory, set_tenant_context
from app.models.activity import Activity
from app.models.client import Client
from app.models.deal import Deal
from app.models.lead import Lead
from app.models.note import Note
from app.models.pipeline import Pipeline, PipelineStage
from app.models.property import Property
from app.models.task import Task
from app.models.user import User

logger = get_logger(__name__)

BATCH_SIZE = 2_000

FIRST_NAMES = [
    "Avery", "Harper", "Marcus", "Priya", "Jonah", "Sofia", "Dara", "Imani",
    "Rowan", "Sana", "Theo", "Nadia", "Omar", "Lena", "Kai", "Mara", "Ivan",
    "Zoe", "Pablo", "Yuki", "Noor", "Elias", "Ruth", "Cole", "Anya", "Ravi",
    "Mia", "Dev", "Tessa", "Bruno", "Hana", "Luca", "Iris", "Enzo", "Nora",
    "Finn",  # magic trailing comma keeps this list expanded past `ruff format`
]
LAST_NAMES = [
    "Chen", "Lindqvist", "Delgado", "Raman", "Whitfield", "Okafor", "Bello",
    "Ellis", "Kaur", "Vega", "Nadella", "Haddad", "Okonkwo", "Bergstrom",
    "Ivanov", "Costa", "Tanaka", "Ahmadi", "Novak", "Fischer", "Moreau",
    "Silva", "Kowalski", "Petrov", "Romano", "Larsen", "Dubois", "Meyer",
    "Santos", "Walsh",
]
CITIES = (
    ("San Francisco", "CA", "941"),
    ("Oakland", "CA", "946"),
    ("Austin", "TX", "787"),
    ("Denver", "CO", "802"),
    ("Seattle", "WA", "981"),
    ("Portland", "OR", "972"),
    ("Chicago", "IL", "606"),
    ("Miami", "FL", "331"),
    ("Boston", "MA", "021"),
    ("Nashville", "TN", "372"),
)
STREETS = (
    "Sanchez", "Folsom", "Townsend", "Alameda", "Birch", "Cedar", "Dolores",
    "Elm", "Grove", "Harrison", "Irving", "Juniper", "Kearny", "Lombard",
    "Mission", "Noe", "Oak", "Pine", "Quince", "Ridge",
)
SOURCES = ("website", "referral", "zillow", "walk_in", "cold_call", "social", "other")
TEMPERATURES = ("hot", "warm", "cold")
LEAD_STAGES = ("new", "contacted", "qualified", "touring", "unqualified")
PROPERTY_TYPES = (
    "single_family", "condo", "townhouse", "multi_family", "land", "commercial",
)
PROPERTY_STATUSES = ("active", "pending", "sold", "off_market", "coming_soon")
CLIENT_TYPES = ("buyer", "seller", "investor", "tenant", "landlord")
ACTIVITY_TYPES = ("call", "email", "meeting", "note", "showing")
TASK_PRIORITIES = ("low", "medium", "high", "urgent")
TASK_STATUSES = ("todo", "in_progress", "blocked", "done")


def _name(rng: random.Random) -> tuple[str, str]:
    return rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)


def _past(rng: random.Random, days: int) -> datetime:
    return datetime.now(UTC) - timedelta(
        days=rng.randint(0, days), hours=rng.randint(0, 23)
    )


def _around_now(rng: random.Random, lo_days: int, hi_days: int) -> datetime:
    """A time from `lo_days` in the past to `hi_days` in the future."""
    return datetime.now(UTC) + timedelta(
        days=rng.randint(-lo_days, hi_days), hours=rng.randint(0, 23)
    )


class Seeder:
    def __init__(self, organization_id: UUID, rng: random.Random) -> None:
        self.org = organization_id
        self.rng = rng
        self.factory = get_session_factory()

    async def _write(self, rows: list[Any]) -> None:
        """One batch, one transaction, tenant context re-bound for RLS."""
        if not rows:
            return
        async with self.factory() as session, session.begin():
            await set_tenant_context(session, self.org)
            session.add_all(rows)

    async def _write_batched(
        self, build: Callable[[], Any], total: int, label: str
    ) -> None:
        start = time.perf_counter()
        done = 0
        while done < total:
            n = min(BATCH_SIZE, total - done)
            await self._write([build() for _ in range(n)])
            done += n
        elapsed = time.perf_counter() - start
        rate = int(done / elapsed) if elapsed else 0
        print(f"  {label:<12} {done:>8,}  ({elapsed:5.1f}s, {rate:,}/s)")

    # ------------------------------------------------------------- entities

    async def agents(self, count: int, existing: list[UUID]) -> list[UUID]:
        ids = list(existing)
        password = hash_password("demo-password-not-for-production")
        batch: list[User] = []
        for i in range(count):
            first, last = _name(self.rng)
            uid = uuid7()
            batch.append(
                User(
                    id=uid,
                    organization_id=self.org,
                    email=f"agent{i}.{uid.hex[:8]}@demo.example",
                    password_hash=password,
                    full_name=f"{first} {last}",
                    job_title="Agent",
                    avatar_hue=self.rng.randint(0, 359),
                    status="active",
                )
            )
            ids.append(uid)
        await self._write(batch)
        print(f"  {'agents':<12} {count:>8,}  (+{len(existing)} existing)")
        return ids

    async def leads(self, total: int, owners: list[UUID]) -> None:
        def build() -> Lead:
            first, last = _name(self.rng)
            lo = self.rng.choice([None, self.rng.randint(3, 15) * 100_000])
            return Lead(
                id=uuid7(),
                organization_id=self.org,
                owner_id=self.rng.choice(owners),
                first_name=first,
                last_name=last,
                email=f"{first}.{last}{self.rng.randint(1, 9999)}@example.com".lower(),
                phone=f"+1{self.rng.randint(2000000000, 9999999999)}",
                stage=self.rng.choice(LEAD_STAGES),
                status=self.rng.choices(("open", "converted", "lost"), (0.7, 0.2, 0.1))[0],
                source=self.rng.choice(SOURCES),
                temperature=self.rng.choice(TEMPERATURES),
                budget_min=Decimal(lo) if lo else None,
                budget_max=Decimal(lo + 400_000) if lo else None,
                preferred_location=self.rng.choice(CITIES)[0],
                score=self.rng.choice([None, self.rng.randint(20, 99)]),
                tags=self.rng.sample(
                    ["vip", "cash", "first-time", "relo"], k=self.rng.randint(0, 2)
                ),
                created_at=_past(self.rng, 365),
            )

        await self._write_batched(build, total, "leads")

    async def clients(self, total: int, owners: list[UUID], out: list[UUID]) -> None:
        def build() -> Client:
            first, last = _name(self.rng)
            cid = uuid7()
            out.append(cid)
            return Client(
                id=cid,
                organization_id=self.org,
                owner_id=self.rng.choice(owners),
                first_name=first,
                last_name=last,
                email=f"{first}.{last}{self.rng.randint(1, 9999)}@example.com".lower(),
                phone=f"+1{self.rng.randint(2000000000, 9999999999)}",
                type=self.rng.choice(CLIENT_TYPES),
                status=self.rng.choice(("active", "under_contract", "dormant", "past")),
                lifetime_value=Decimal(self.rng.randint(0, 5000) * 1000),
                created_at=_past(self.rng, 730),
            )

        await self._write_batched(build, total, "clients")

    async def properties(self, total: int, owners: list[UUID], out: list[UUID]) -> None:
        def build() -> Property:
            city, state, zip3 = self.rng.choice(CITIES)
            pid = uuid7()
            out.append(pid)
            return Property(
                id=pid,
                organization_id=self.org,
                listing_agent_id=self.rng.choice(owners),
                title=f"{self.rng.randint(1, 4999)} {self.rng.choice(STREETS)} St",
                status=self.rng.choice(PROPERTY_STATUSES),
                property_type=self.rng.choice(PROPERTY_TYPES),
                address_line1=f"{self.rng.randint(1, 4999)} {self.rng.choice(STREETS)} St",
                city=city,
                state=state,
                postal_code=f"{zip3}{self.rng.randint(10, 99)}",
                price=Decimal(self.rng.randint(3, 40) * 100_000),
                bedrooms=self.rng.randint(0, 6),
                bathrooms=Decimal(self.rng.choice(["1.0", "1.5", "2.0", "2.5", "3.0"])),
                square_feet=self.rng.randint(600, 6000),
                year_built=self.rng.randint(1900, 2025),
                created_at=_past(self.rng, 500),
            )

        await self._write_batched(build, total, "properties")

    async def deals(
        self,
        total: int,
        owners: list[UUID],
        clients: list[UUID],
        properties: list[UUID],
        stages: list[tuple[UUID, int]],
        pipeline_id: UUID,
    ) -> None:
        def build() -> Deal:
            stage_id, prob = self.rng.choice(stages)
            value = Decimal(self.rng.randint(3, 40) * 100_000)
            has_property = properties and self.rng.random() < 0.6
            return Deal(
                id=uuid7(),
                organization_id=self.org,
                owner_id=self.rng.choice(owners),
                client_id=self.rng.choice(clients),
                property_id=self.rng.choice(properties) if has_property else None,
                pipeline_id=pipeline_id,
                stage_id=stage_id,
                title=f"{self.rng.choice(FIRST_NAMES)} — {self.rng.choice(STREETS)} deal",
                value=value,
                probability=prob,
                priority=self.rng.choice(TASK_PRIORITIES),
                created_at=_past(self.rng, 400),
            )

        await self._write_batched(build, total, "deals")

    async def tasks(self, total: int, owners: list[UUID], links: list[tuple[str, UUID]]) -> None:
        def build() -> Task:
            done = self.rng.random() < 0.35
            link = self.rng.choice(links) if links and self.rng.random() < 0.5 else None
            return Task(
                id=uuid7(),
                organization_id=self.org,
                assignee_id=self.rng.choice(owners),
                created_by=self.rng.choice(owners),
                title=self.rng.choice(
                    ["Follow up", "Send docs", "Schedule showing", "Call back",
                     "Prep CMA", "Order inspection", "Review offer"]
                ),
                status="done" if done else self.rng.choice(TASK_STATUSES[:3]),
                priority=self.rng.choice(TASK_PRIORITIES),
                # A spread of overdue and upcoming, so the dashboard's overdue
                # count and the work queue are both exercised.
                due_at=_around_now(self.rng, 30, 30),
                completed_at=_past(self.rng, 30) if done else None,
                entity_type=link[0] if link else None,
                entity_id=link[1] if link else None,
                created_at=_past(self.rng, 200),
            )

        await self._write_batched(build, total, "tasks")

    async def activities(
        self, total: int, owners: list[UUID], links: list[tuple[str, UUID]]
    ) -> None:
        def build() -> Activity:
            etype, eid = self.rng.choice(links)
            occurred = _past(self.rng, 365)
            return Activity(
                id=uuid7(),
                organization_id=self.org,
                actor_id=self.rng.choice(owners),
                entity_type=etype,
                entity_id=eid,
                type=self.rng.choice(ACTIVITY_TYPES),
                subject=self.rng.choice(
                    ["Left a voicemail", "Sent the brochure", "Toured the property",
                     "Discussed financing", "Followed up by email", "Met for coffee"]
                ),
                body=None,
                occurred_at=occurred,
                created_at=occurred,
            )

        await self._write_batched(build, total, "activities")

    async def notes(self, total: int, owners: list[UUID], links: list[tuple[str, UUID]]) -> None:
        def build() -> Note:
            etype, eid = self.rng.choice(links)
            return Note(
                id=uuid7(),
                organization_id=self.org,
                author_id=self.rng.choice(owners),
                entity_type=etype,
                entity_id=eid,
                body=self.rng.choice(
                    ["Buyer is pre-approved and motivated to close quickly.",
                     "Seller wants to stay through end of month; flexible on price.",
                     "Prefers north-facing units with parking.",
                     "Financing contingency waived; strong offer expected."]
                ),
                is_pinned=self.rng.random() < 0.1,
                created_at=_past(self.rng, 300),
            )

        await self._write_batched(build, total, "notes")


async def _lookup_org_id(org_slug: str | None) -> UUID | None:
    """Resolve an org id without knowing it first.

    `organizations` is FORCE RLS keyed by `id = current_organization_id()`, so a
    lookup by slug is the bootstrap chicken-and-egg: you cannot read the row
    until you already know its id. The migration role owns the table and can
    lift FORCE for the duration of the read — exactly what the pipelines
    migration does in `_seed_default_pipelines`. Restored immediately after.
    """
    settings = get_settings()
    url = (
        f"postgresql+asyncpg://{settings.POSTGRES_MIGRATION_USER}:"
        f"{settings.POSTGRES_MIGRATION_PASSWORD.get_secret_value()}@"
        f"{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text("ALTER TABLE organizations NO FORCE ROW LEVEL SECURITY")
            )
            try:
                query = "SELECT id FROM organizations"
                params: dict[str, str] = {}
                if org_slug:
                    query += " WHERE slug = :slug"
                    params["slug"] = org_slug
                query += " ORDER BY created_at LIMIT 1"
                row = (await conn.execute(text(query), params)).first()
            finally:
                await conn.execute(
                    text("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
                )
        return row[0] if row else None
    finally:
        await engine.dispose()


async def _resolve(
    org_slug: str | None,
) -> tuple[UUID, UUID, list[tuple[UUID, int]], list[UUID]]:
    """Find the target org, its default pipeline + stages, and existing users."""
    org_id = await _lookup_org_id(org_slug)
    if org_id is None:
        raise SystemExit(
            "No matching organization. Run app.cli.bootstrap first, or pass --org-slug."
        )

    factory = get_session_factory()
    async with factory() as session, session.begin():
        await set_tenant_context(session, org_id)
        pipeline = (
            await session.execute(
                select(Pipeline)
                .where(Pipeline.organization_id == org_id)
                .where(Pipeline.is_default.is_(True))
            )
        ).scalar_one_or_none()
        if pipeline is None:
            raise SystemExit("The organization has no default pipeline.")
        stages = [
            (s.id, s.default_probability)
            for s in (
                await session.execute(
                    select(PipelineStage).where(PipelineStage.pipeline_id == pipeline.id)
                )
            ).scalars()
        ]
        users = list(
            (
                await session.execute(
                    select(User.id).where(User.organization_id == org_id)
                )
            ).scalars()
        )
    return org_id, pipeline.id, stages, users


async def seed(*, org_slug: str | None, scale: float, seed_value: int) -> None:
    rng = random.Random(seed_value)  # noqa: S311 - demo data, not cryptographic
    org_id, pipeline_id, stages, existing_users = await _resolve(org_slug)

    n_agents = max(5, int(20 * scale))
    n_leads = int(100_000 * scale)
    n_clients = int(25_000 * scale)
    n_properties = int(15_000 * scale)
    n_deals = int(20_000 * scale)
    n_tasks = int(20_000 * scale)
    n_activities = int(50_000 * scale)
    n_notes = int(20_000 * scale)

    print(
        f"Seeding org {org_id} at scale {scale} "
        f"(~{n_leads + n_clients + n_properties + n_deals + n_tasks + n_activities + n_notes:,}"
        " rows)\n"
    )
    started = time.perf_counter()
    seeder = Seeder(org_id, rng)

    owners = await seeder.agents(n_agents, existing_users)

    client_ids: list[UUID] = []
    property_ids: list[UUID] = []
    await seeder.leads(n_leads, owners)
    await seeder.clients(n_clients, owners, client_ids)
    await seeder.properties(n_properties, owners, property_ids)
    await seeder.deals(n_deals, owners, client_ids, property_ids, stages, pipeline_id)

    # Parents that activities, tasks and notes can hang off. Capped samples keep
    # the link pool in memory bounded regardless of scale.
    cap = 5_000
    links: list[tuple[str, UUID]] = (
        [("client", c) for c in client_ids[:cap]]
        + [("property", p) for p in property_ids[:cap]]
    )
    await seeder.tasks(n_tasks, owners, links)
    if links:
        await seeder.activities(n_activities, owners, links)
        await seeder.notes(n_notes, owners, links)
    else:
        print("  (skipped activities/notes — no parent records at this scale)")

    elapsed = time.perf_counter() - started
    print(f"\nDone in {elapsed:.1f}s.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a large demo dataset.")
    parser.add_argument("--org-slug", default=None, help="Target org (default: the only/first one)")
    parser.add_argument("--scale", type=float, default=1.0, help="1.0 ≈ 100k leads")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    asyncio.run(seed(org_slug=args.org_slug, scale=args.scale, seed_value=args.seed))


if __name__ == "__main__":
    main()
