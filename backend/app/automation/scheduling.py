"""Delays, business hours, and when a parked run wakes up.

A delay node parks the run — status `waiting`, a `resume_at`, no worker held —
and a sweep picks it back up. That is the only design that survives a workflow
saying "wait three days": holding a coroutine open is not an option, and neither
is a job with a three-day visibility timeout.

**Business hours are the interesting part.** "Wait one hour" from 4:45pm on a
Friday should not mean 5:45pm on a Friday if the next step emails a client. The
`business_hours` option advances the resume time to the next working moment
instead.

Two decisions inside that:

* **The window is workspace-wide configuration, not per workflow.** An agency
  has opening hours; a workflow does not. Per-workflow hours would be five
  copies of the same fact, four of which are wrong after the first change.
* **Weekends and the window are honoured; public holidays are not.** Holidays
  need a calendar per country and per region, and getting them subtly wrong is
  worse than not claiming to handle them — a workflow that fires on Boxing Day
  is a smaller surprise than one that silently skips a working day in a country
  whose holidays we guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Monday is 0, matching `datetime.weekday()`.
DEFAULT_WORKING_DAYS = (0, 1, 2, 3, 4)


@dataclass(frozen=True, slots=True)
class BusinessHours:
    """The workspace's working window, in UTC.

    UTC rather than a named timezone because every timestamp in this system is
    `timestamptz` and the conversion has to happen somewhere; doing it once at
    configuration time beats doing it in every comparison and getting one wrong.
    The cost is that a workspace changing its hours across a DST boundary has to
    say so — which is a setting change, not a silent drift.
    """

    start_hour: int = 9
    end_hour: int = 18
    working_days: tuple[int, ...] = DEFAULT_WORKING_DAYS

    def contains(self, moment: datetime) -> bool:
        if moment.weekday() not in self.working_days:
            return False
        return self.start_hour <= moment.hour < self.end_hour

    def next_open(self, moment: datetime) -> datetime:
        """The first working instant at or after `moment`.

        Bounded to a fortnight of lookahead: a configuration with no working
        days at all would otherwise loop forever, and a run parked until the
        heat death of the universe is a worse failure than one that fires late
        and logs why.
        """
        if self.contains(moment):
            return moment

        candidate = moment
        for _ in range(14):
            if candidate.weekday() not in self.working_days:
                candidate = _start_of_next_day(candidate, self.start_hour)
                continue
            if candidate.hour < self.start_hour:
                return candidate.replace(
                    hour=self.start_hour, minute=0, second=0, microsecond=0
                )
            if candidate.hour >= self.end_hour:
                candidate = _start_of_next_day(candidate, self.start_hour)
                continue
            return candidate

        logger.warning(
            "business_hours_unsatisfiable",
            extra={"working_days": list(self.working_days)},
        )
        return moment


def _start_of_next_day(moment: datetime, hour: int) -> datetime:
    return (moment + timedelta(days=1)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def resume_at(
    *,
    minutes: int,
    business_hours: BusinessHours | None = None,
    now: datetime | None = None,
) -> datetime:
    """When a delay node's run should wake.

    With business hours, the elapsed time is measured in *working* minutes: a
    two-hour delay starting at 5pm resumes at 10am, not 7pm. Counting wall-clock
    minutes and then shifting into hours would make "wait 2 hours" mean "wait
    17 hours" on a Friday evening, which is not what anybody configured.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)

    if business_hours is None:
        return moment + timedelta(minutes=minutes)

    cursor = business_hours.next_open(moment)
    remaining = minutes

    # Walk forward window by window. Bounded by the delay itself, which
    # validation caps at 30 days.
    for _ in range(400):
        if remaining <= 0:
            return cursor
        window_end = cursor.replace(
            hour=business_hours.end_hour, minute=0, second=0, microsecond=0
        )
        available = int((window_end - cursor).total_seconds() // 60)
        if remaining <= available:
            return cursor + timedelta(minutes=remaining)
        remaining -= available
        cursor = business_hours.next_open(
            _start_of_next_day(cursor, business_hours.start_hour)
        )

    logger.warning("delay_exceeded_business_hours_window")
    return cursor


def parse_business_hours(config: dict[str, Any] | None) -> BusinessHours | None:
    """Read a delay node's business-hours setting.

    Absent or false means wall-clock, which is the right default: most delays
    are "check back in 15 minutes", and forcing those into working hours would
    make a workflow that runs at 8:55am wait an hour for no reason.
    """
    if not config or not config.get("business_hours"):
        return None
    return BusinessHours(
        start_hour=int(config.get("start_hour", 9)),
        end_hour=int(config.get("end_hour", 18)),
    )


__all__ = ["BusinessHours", "parse_business_hours", "resume_at"]
