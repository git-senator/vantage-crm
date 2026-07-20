import type { Metadata } from "next";
import { ChevronLeft, ChevronRight, Clock, MapPin, Plus } from "lucide-react";

import { PageHeader } from "@/components/shared/page-header";
import { AvatarStack } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { calendarEvents } from "@/lib/mock-data";
import { cn } from "@/lib/utils";
import type { EventKind } from "@/types";

export const metadata: Metadata = { title: "Calendar" };

const kindStyles: Record<EventKind, { chip: string; dot: string; label: string }> = {
  showing: { chip: "bg-info/12 text-info", dot: "bg-info", label: "Showing" },
  call: { chip: "bg-primary/10 text-primary", dot: "bg-primary", label: "Call" },
  closing: { chip: "bg-success/12 text-success", dot: "bg-success", label: "Closing" },
  "open-house": { chip: "bg-warning/18 text-warning-foreground dark:bg-warning/20 dark:text-warning", dot: "bg-warning", label: "Open house" },
  internal: { chip: "bg-muted text-muted-foreground", dot: "bg-muted-foreground/60", label: "Internal" },
};

const TODAY = "2026-07-20";
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/**
 * July 2026 starts on a Wednesday, so the Monday-first grid opens with two
 * trailing days from June. Hard-coded because the prototype has no date engine.
 */
function buildMonthGrid() {
  const cells: { date: string | null; day: number; muted: boolean }[] = [];

  for (const day of [29, 30]) {
    cells.push({ date: null, day, muted: true });
  }
  for (let day = 1; day <= 31; day++) {
    cells.push({
      date: `2026-07-${String(day).padStart(2, "0")}`,
      day,
      muted: false,
    });
  }
  // Pad to a whole number of weeks with early August.
  let next = 1;
  while (cells.length % 7 !== 0) {
    cells.push({ date: null, day: next++, muted: true });
  }
  return cells;
}

export default function CalendarPage() {
  const cells = buildMonthGrid();
  const upcoming = [...calendarEvents].sort((a, b) =>
    a.date === b.date ? a.start.localeCompare(b.start) : a.date.localeCompare(b.date),
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Calendar"
        description="Showings, closings and open houses across the whole team."
        actions={
          <>
            <Tabs defaultValue="month">
              <TabsList>
                <TabsTrigger value="day">Day</TabsTrigger>
                <TabsTrigger value="week">Week</TabsTrigger>
                <TabsTrigger value="month">Month</TabsTrigger>
              </TabsList>
            </Tabs>
            <Button>
              <Plus className="size-4" />
              New event
            </Button>
          </>
        }
      />

      <div className="grid gap-6 xl:grid-cols-[1fr_320px]">
        {/* ------------------------------------------------- month grid */}
        <Card className="gap-0 overflow-hidden py-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
            <div className="flex items-center gap-2">
              <Button variant="outline" size="icon-sm" aria-label="Previous month">
                <ChevronLeft className="size-4" />
              </Button>
              <Button variant="outline" size="icon-sm" aria-label="Next month">
                <ChevronRight className="size-4" />
              </Button>
              <h2 className="ml-1 text-base font-semibold">July 2026</h2>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              {Object.entries(kindStyles).map(([kind, style]) => (
                <span
                  key={kind}
                  className="flex items-center gap-1.5 text-xs text-muted-foreground"
                >
                  <span className={cn("size-2 rounded-full", style.dot)} />
                  {style.label}
                </span>
              ))}
              <Button variant="outline" size="sm">
                Today
              </Button>
            </div>
          </div>

          <div className="grid grid-cols-7 border-b bg-muted/40">
            {WEEKDAYS.map((day) => (
              <div
                key={day}
                className="px-2 py-2 text-center text-xs font-medium text-muted-foreground"
              >
                {day}
              </div>
            ))}
          </div>

          <div className="grid grid-cols-7">
            {cells.map((cell, index) => {
              const dayEvents = cell.date
                ? calendarEvents.filter((e) => e.date === cell.date)
                : [];
              const isToday = cell.date === TODAY;

              return (
                <div
                  key={index}
                  className={cn(
                    "min-h-[104px] border-r border-b p-1.5 last:border-r-0",
                    cell.muted && "bg-muted/30",
                    index % 7 === 6 && "border-r-0",
                  )}
                >
                  <span
                    className={cn(
                      "tabular grid size-6 place-items-center rounded-full text-xs",
                      isToday
                        ? "bg-primary font-semibold text-primary-foreground"
                        : cell.muted
                          ? "text-muted-foreground/50"
                          : "text-muted-foreground",
                    )}
                  >
                    {cell.day}
                  </span>

                  <div className="mt-1 space-y-1">
                    {dayEvents.slice(0, 2).map((event) => (
                      <div
                        key={event.id}
                        className={cn(
                          "truncate rounded px-1.5 py-0.5 text-[11px] font-medium",
                          kindStyles[event.kind].chip,
                        )}
                        title={`${event.start} ${event.title}`}
                      >
                        {event.start} {event.title}
                      </div>
                    ))}
                    {dayEvents.length > 2 && (
                      <p className="px-1.5 text-[11px] text-muted-foreground">
                        +{dayEvents.length - 2} more
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </Card>

        {/* --------------------------------------------------- upcoming */}
        <Card className="h-fit">
          <CardHeader>
            <CardTitle>Upcoming</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {upcoming.map((event) => (
              <div
                key={event.id}
                className="rounded-lg border p-3 transition-colors hover:bg-muted/50"
              >
                <div className="flex items-start gap-2">
                  <span
                    className={cn(
                      "mt-1.5 size-2 shrink-0 rounded-full",
                      kindStyles[event.kind].dot,
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm leading-snug font-medium">
                      {event.title}
                    </p>
                    <p className="tabular mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Clock className="size-3.5" />
                      {new Date(`${event.date}T00:00:00`).toLocaleDateString(
                        "en-US",
                        { month: "short", day: "numeric" },
                      )}
                      {" · "}
                      {event.start}–{event.end}
                    </p>
                    {event.location && (
                      <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                        <MapPin className="size-3.5 shrink-0" />
                        <span className="truncate">{event.location}</span>
                      </p>
                    )}
                    <div className="mt-2">
                      <AvatarStack users={event.attendees} max={4} />
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
