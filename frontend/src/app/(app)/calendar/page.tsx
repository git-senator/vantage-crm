import type { Metadata } from "next";
import Link from "next/link";
import { CalendarDays, ChevronLeft, ChevronRight, Clock, MapPin } from "lucide-react";

import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { listCalendarEvents } from "@/lib/api/calendar";
import type { CalendarEvent, CalendarEventType } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Calendar" };

const TYPE_STYLES: Record<
  CalendarEventType,
  { chip: string; dot: string; label: string }
> = {
  showing: { chip: "bg-info/12 text-info", dot: "bg-info", label: "Showing" },
  call: { chip: "bg-primary/10 text-primary", dot: "bg-primary", label: "Call" },
  meeting: {
    chip: "bg-muted text-muted-foreground",
    dot: "bg-muted-foreground/60",
    label: "Meeting",
  },
  closing: {
    chip: "bg-success/12 text-success",
    dot: "bg-success",
    label: "Closing",
  },
  open_house: {
    chip: "bg-warning/18 text-warning-foreground dark:bg-warning/20 dark:text-warning",
    dot: "bg-warning",
    label: "Open house",
  },
  personal: {
    chip: "bg-muted text-muted-foreground",
    dot: "bg-muted-foreground/60",
    label: "Personal",
  },
};

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/**
 * A Monday-first grid covering the month, padded to whole weeks.
 *
 * Computed rather than hard-coded — the prototype pinned July 2026 because it
 * had no date engine; a live calendar has to answer for every month.
 */
function buildMonthGrid(year: number, month: number) {
  const first = new Date(Date.UTC(year, month, 1));
  // getUTCDay is Sunday-first; shift so Monday is 0.
  const leading = (first.getUTCDay() + 6) % 7;
  const daysInMonth = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();

  const cells: { key: string; day: number; iso: string | null }[] = [];
  for (let i = 0; i < leading; i++) {
    cells.push({ key: `lead-${i}`, day: 0, iso: null });
  }
  for (let day = 1; day <= daysInMonth; day++) {
    const iso = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    cells.push({ key: iso, day, iso });
  }
  while (cells.length % 7 !== 0) {
    cells.push({ key: `trail-${cells.length}`, day: 0, iso: null });
  }
  return cells;
}

function monthWindow(year: number, month: number) {
  // Padded a week either side so events spilling over a month boundary still
  // appear in the cells the grid actually shows.
  const start = new Date(Date.UTC(year, month, 1) - 7 * 86_400_000);
  const end = new Date(Date.UTC(year, month + 1, 1) + 7 * 86_400_000);
  return { start: start.toISOString(), end: end.toISOString() };
}

function localDateKey(iso: string): string {
  const date = new Date(iso);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
    date.getDate(),
  ).padStart(2, "0")}`;
}

/**
 * The calendar, on live data since Phase 3.5.
 *
 * The visible month is a search param, so navigation is server-rendered and
 * linkable — the same choice the inbox and every detail view make.
 */
export default async function CalendarPage({
  searchParams,
}: {
  searchParams: Promise<{ m?: string }>;
}) {
  const { m } = await searchParams;
  const now = new Date();
  const parsed = m?.match(/^(\d{4})-(\d{2})$/);
  const year = parsed ? Number(parsed[1]) : now.getFullYear();
  const month = parsed ? Number(parsed[2]) - 1 : now.getMonth();

  const events = await listCalendarEvents(monthWindow(year, month));

  const byDay = new Map<string, CalendarEvent[]>();
  for (const event of events) {
    const key = localDateKey(event.starts_at);
    byDay.set(key, [...(byDay.get(key) ?? []), event]);
  }

  const cells = buildMonthGrid(year, month);
  const label = new Date(Date.UTC(year, month, 1)).toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
  const previous = new Date(Date.UTC(year, month - 1, 1));
  const next = new Date(Date.UTC(year, month + 1, 1));
  const href = (date: Date) =>
    `/calendar?m=${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;

  const todayKey = localDateKey(now.toISOString());
  const upcoming = events
    .filter((event) => new Date(event.starts_at) >= now)
    .slice(0, 8);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Calendar"
        description="Showings, closings and open houses across the whole team."
      />

      <div className="grid gap-6 xl:grid-cols-[1fr_320px]">
        <Card className="gap-0 overflow-hidden py-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="icon-sm"
                aria-label="Previous month"
                render={<Link href={href(previous)} />}
              >
                <ChevronLeft className="size-4" />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                aria-label="Next month"
                render={<Link href={href(next)} />}
              >
                <ChevronRight className="size-4" />
              </Button>
              <h2 className="ml-1 text-base font-semibold">{label}</h2>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              {Object.entries(TYPE_STYLES).map(([type, style]) => (
                <span
                  key={type}
                  className="flex items-center gap-1.5 text-xs text-muted-foreground"
                >
                  <span className={cn("size-2 rounded-full", style.dot)} />
                  {style.label}
                </span>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-7 border-b">
            {WEEKDAYS.map((day) => (
              <div
                key={day}
                className="p-2 text-center text-[11px] font-medium text-muted-foreground"
              >
                {day}
              </div>
            ))}
          </div>

          <div className="grid grid-cols-7">
            {cells.map((cell) => {
              const dayEvents = cell.iso ? (byDay.get(cell.iso) ?? []) : [];
              return (
                <div
                  key={cell.key}
                  className={cn(
                    "min-h-24 border-r border-b p-1.5 last:border-r-0",
                    !cell.iso && "bg-muted/30",
                    cell.iso === todayKey && "bg-accent/30",
                  )}
                >
                  {cell.iso && (
                    <span
                      className={cn(
                        "text-[11px]",
                        cell.iso === todayKey
                          ? "font-semibold"
                          : "text-muted-foreground",
                      )}
                    >
                      {cell.day}
                    </span>
                  )}
                  <div className="mt-1 space-y-1">
                    {dayEvents.slice(0, 3).map((event) => {
                      const style =
                        TYPE_STYLES[event.event_type] ?? TYPE_STYLES.meeting;
                      return (
                        <p
                          key={event.id}
                          className={cn(
                            "truncate rounded px-1 py-0.5 text-[10px]",
                            style.chip,
                            event.status === "tentative" && "opacity-60",
                          )}
                          title={event.title}
                        >
                          {event.title}
                        </p>
                      );
                    })}
                    {dayEvents.length > 3 && (
                      <p className="px-1 text-[10px] text-muted-foreground">
                        +{dayEvents.length - 3} more
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Coming up</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {upcoming.length === 0 ? (
              <EmptyState
                compact
                icon={CalendarDays}
                title="Nothing scheduled"
                description="Showings and closings booked against a record appear here."
              />
            ) : (
              upcoming.map((event) => {
                const style = TYPE_STYLES[event.event_type] ?? TYPE_STYLES.meeting;
                return (
                  <div key={event.id} className="flex gap-2.5">
                    <span
                      className={cn("mt-1.5 size-2 shrink-0 rounded-full", style.dot)}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{event.title}</p>
                      <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
                        <Clock className="size-3" />
                        {new Date(event.starts_at).toLocaleString(undefined, {
                          day: "numeric",
                          month: "short",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                        {event.status === "tentative" && " · tentative"}
                      </p>
                      {event.location && (
                        <p className="flex items-center gap-1 truncate text-[11px] text-muted-foreground">
                          <MapPin className="size-3 shrink-0" />
                          {event.location}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
