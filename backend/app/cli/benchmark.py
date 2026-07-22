"""Measure list-endpoint latency against a seeded workspace.

    python -m app.cli.seed_demo --scale 1.0     # ~100k leads
    python -m app.cli.benchmark

Phase 2's exit criterion is **p95 list latency < 200 ms on 100k rows**. This
harness drives the repository `list_page` path — the query and the index, which
is where that time is spent — rather than the HTTP layer, so the number is the
database's, not the network's or the serializer's. Each list is measured at ALL
scope (`owner_ids=None`): the largest result set and the hardest case for the
planner, since there is no owner predicate to narrow the scan.

Two shapes per entity: the default first page (keyset, no cursor) and a
full-text search, because they exercise different indexes.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from collections.abc import Awaitable, Callable

from sqlalchemy import func, select

from app.cli.seed_demo import _lookup_org_id
from app.core.logging import configure_logging
from app.db.session import get_session_factory, set_tenant_context
from app.models.activity import Activity
from app.models.client import Client
from app.models.deal import Deal
from app.models.lead import Lead
from app.models.note import Note
from app.models.property import Property
from app.models.task import Task
from app.repositories.activity import ActivityRepository
from app.repositories.client import ClientRepository
from app.repositories.deal import DealRepository
from app.repositories.lead import LeadRepository
from app.repositories.note import NoteRepository
from app.repositories.property import PropertyRepository
from app.repositories.task import TaskRepository
from app.schemas.activity import ActivityFilters
from app.schemas.client import ClientFilters
from app.schemas.deal import DealFilters
from app.schemas.lead import LeadFilters
from app.schemas.note import NoteFilters
from app.schemas.property import PropertyFilters
from app.schemas.task import TaskFilters

PAGE_SIZE = 25
THRESHOLD_MS = 200.0


def _percentile(samples: list[float], pct: float) -> float:
    ordered = sorted(samples)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


async def _time(fn: Callable[[], Awaitable[object]], runs: int) -> list[float]:
    times: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        await fn()
        times.append((time.perf_counter() - start) * 1000)
    return times


async def benchmark(*, org_slug: str | None, runs: int) -> None:
    org_id = await _lookup_org_id(org_slug)
    if org_id is None:
        raise SystemExit("No matching organization. Seed one first.")

    factory = get_session_factory()
    async with factory() as session, session.begin():
        await set_tenant_context(session, org_id)

        # Row counts for context — the whole point is that these are large.
        counts = {}
        for label, model in (
            ("leads", Lead), ("clients", Client), ("properties", Property),
            ("deals", Deal), ("tasks", Task), ("activities", Activity),
            ("notes", Note),
        ):
            counts[label] = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(model)
                        .where(model.organization_id == org_id)
                    )
                ).scalar()
                or 0
            )
        print("Row counts:")
        for label, n in counts.items():
            print(f"  {label:<12} {n:>10,}")
        print(f"\nMeasuring {runs} runs each, ALL scope, page size {PAGE_SIZE}.\n")

        leads = LeadRepository(session)
        clients = ClientRepository(session)
        properties = PropertyRepository(session)
        deals = DealRepository(session)
        tasks = TaskRepository(session)
        activities = ActivityRepository(session)
        notes = NoteRepository(session)

        # (label, callable). `None` scope = ALL, the worst case.
        cases: list[tuple[str, Callable[[], Awaitable[object]]]] = [
            ("leads: page", lambda: leads.list_page(
                org_id, owner_ids=None, filters=LeadFilters(), limit=PAGE_SIZE)),
            ("leads: search", lambda: leads.list_page(
                org_id, owner_ids=None, filters=LeadFilters(search="Chen"), limit=PAGE_SIZE)),
            ("clients: page", lambda: clients.list_page(
                org_id, owner_ids=None, filters=ClientFilters(), limit=PAGE_SIZE)),
            ("properties: page", lambda: properties.list_page(
                org_id, agent_ids=None, filters=PropertyFilters(), limit=PAGE_SIZE)),
            ("properties: search", lambda: properties.list_page(
                org_id, agent_ids=None, filters=PropertyFilters(search="Oak"), limit=PAGE_SIZE)),
            ("deals: page", lambda: deals.list_page(
                org_id, owner_ids=None, filters=DealFilters(), limit=PAGE_SIZE)),
            ("deals: status", lambda: deals.list_page(
                org_id, owner_ids=None, filters=DealFilters(status="open"), limit=PAGE_SIZE)),
            ("tasks: page", lambda: tasks.list_page(
                org_id, assignee_ids=None, filters=TaskFilters(), limit=PAGE_SIZE)),
            ("tasks: overdue", lambda: tasks.list_page(
                org_id, assignee_ids=None, filters=TaskFilters(overdue=True), limit=PAGE_SIZE)),
            ("activities: feed", lambda: activities.list_page(
                org_id, filters=ActivityFilters(), limit=PAGE_SIZE)),
            ("notes: feed", lambda: notes.list_page(
                org_id, filters=NoteFilters(), limit=PAGE_SIZE)),
        ]

        print(f"{'endpoint':<22}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}   verdict")
        print("-" * 72)
        worst_p95 = 0.0
        for label, fn in cases:
            await fn()  # warm the plan/cache once, not counted
            samples = await _time(fn, runs)
            p50 = statistics.median(samples)
            p95 = _percentile(samples, 95)
            p99 = _percentile(samples, 99)
            worst_p95 = max(worst_p95, p95)
            ok = "ok" if p95 < THRESHOLD_MS else "SLOW"
            print(
                f"{label:<22}{p50:>8.1f}{p95:>9.1f}{p99:>9.1f}{max(samples):>9.1f}   {ok}"
            )

        print("-" * 72)
        verdict = "PASS" if worst_p95 < THRESHOLD_MS else "FAIL"
        print(
            f"\nWorst p95: {worst_p95:.1f} ms — {verdict} "
            f"(criterion: p95 < {THRESHOLD_MS:.0f} ms)."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark list-endpoint latency.")
    parser.add_argument("--org-slug", default=None)
    parser.add_argument("--runs", type=int, default=50)
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    asyncio.run(benchmark(org_slug=args.org_slug, runs=args.runs))


if __name__ == "__main__":
    main()
