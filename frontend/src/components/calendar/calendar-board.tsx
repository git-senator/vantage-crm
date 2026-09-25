"use client";

import { useState } from "react";
import Link from "next/link";
import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock,
  MapPin,
  Plus,
} from "lucide-react";

import {
  EventDialog,
  type LinkOptions,
} from "@/components/calendar/event-dialog";
import { EmptyState } from "@/components/shared/empty-state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTranslation } from "@/i18n/language-provider";
import type { CalendarEvent, CalendarEventType } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/** Visual styles only; the readable label comes through `t()`. */
const TYPE_STYLES: Record<CalendarEventType, { chip: string; dot: string }> = {
  showing: { chip: "bg-info/12 text-info", dot: "bg-info" },
  call: { chip: "bg-primary/10 text-primary", dot: "bg-primary" },
  meeting: {
    chip: "bg-muted text-muted-foreground",
    dot: "bg-muted-foreground/60",
  },
  closing: { chip: "bg-success/12 text-success", dot: "bg-success" },
  open_house: {
    chip: "bg-warning/18 text-warning-foreground dark:bg-warning/20 dark:text-warning",
    dot: "bg-warning",
  },
  personal: {
    chip: "bg-muted text-muted-foreground",
    dot: "bg-muted-foreground/60",
  },
};

export type MonthCell = { key: string; day: number; iso: string | null };

/**
 * The month grid and the what's-next list, sharing one dialog.
 *
 * The month itself is still decided on the server through the `m` search
 * param, so navigation stays linkable; only the parts a person clicks live in
 * the browser. Both panels open the same dialog, which is why they are one
 * component rather than two that each own a copy of it.
 */
export function CalendarBoard({
  cells,
  weekdays,
  monthLabel,
  previousHref,
  nextHref,
  byDay,
  upcoming,
  todayKey,
  bcp,
  canManage,
  links,
  openOnArrival = false,
}: {
  cells: MonthCell[];
  weekdays: string[];
  monthLabel: string;
  previousHref: string;
  nextHref: string;
  /** Events keyed by local day, as the grid needs them. */
  byDay: Record<string, CalendarEvent[]>;
  upcoming: CalendarEvent[];
  todayKey: string;
  /** BCP-47 tag for date formatting in the active language. */
  bcp: string;
  canManage: boolean;
  links: LinkOptions;
  /** Arrived from the Create menu, which has no page of its own to send you to. */
  openOnArrival?: boolean;
}) {
  const { t } = useTranslation();
  // The Create menu links here with a flag rather than to a page, because an
  // event is a dialog and not a route; so it simply starts open.
  const [open, setOpen] = useState(openOnArrival && canManage);
  const [editing, setEditing] = useState<CalendarEvent | null>(null);
  const [day, setDay] = useState<string | null>(null);


  function openNew(on: string | null) {
    if (!canManage) return;
    setEditing(null);
    setDay(on);
    setOpen(true);
  }

  function openEvent(event: CalendarEvent) {
    setEditing(event);
    setDay(null);
    setOpen(true);
  }

  return (
    <>
      <div className="grid gap-6 xl:grid-cols-[1fr_320px]">
        <Card className="gap-0 overflow-hidden py-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="icon-sm"
                aria-label={t("body.calPrevMonth")}
                render={<Link href={previousHref} />}
              >
                <ChevronLeft className="size-4" />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                aria-label={t("body.calNextMonth")}
                render={<Link href={nextHref} />}
              >
                <ChevronRight className="size-4" />
              </Button>
              <h2 className="ml-1 text-base font-semibold">{monthLabel}</h2>
              {canManage && (
                <Button
                  size="sm"
                  className="ml-2"
                  onClick={() => openNew(null)}
                >
                  <Plus className="size-4" />
                  {t("body.calNewEvent")}
                </Button>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-3">
              {Object.entries(TYPE_STYLES).map(([type, style]) => (
                <span
                  key={type}
                  className="flex items-center gap-1.5 text-xs text-muted-foreground"
                >
                  <span className={cn("size-2 rounded-full", style.dot)} />
                  {t(`body.calType_${type}`)}
                </span>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-7 border-b">
            {weekdays.map((weekday) => (
              <div
                key={weekday}
                className="p-2 text-center text-[11px] font-medium text-muted-foreground"
              >
                {weekday}
              </div>
            ))}
          </div>

          <div className="grid grid-cols-7">
            {cells.map((cell) => {
              const dayEvents = cell.iso ? (byDay[cell.iso] ?? []) : [];
              return (
                <div
                  key={cell.key}
                  // The day is a click target for adding, so it carries the
                  // role; the events inside it stop the click from bubbling
                  // and opening a blank form on top of the one being read.
                  role={cell.iso && canManage ? "button" : undefined}
                  tabIndex={cell.iso && canManage ? 0 : undefined}
                  aria-label={cell.iso ?? undefined}
                  onClick={() => cell.iso && openNew(cell.iso)}
                  onKeyDown={(keyed) => {
                    if (cell.iso && (keyed.key === "Enter" || keyed.key === " ")) {
                      keyed.preventDefault();
                      openNew(cell.iso);
                    }
                  }}
                  className={cn(
                    "group/day relative min-h-24 border-r border-b p-1.5 last:border-r-0",
                    !cell.iso && "bg-muted/30",
                    cell.iso === todayKey && "bg-accent/30",
                    cell.iso &&
                      canManage &&
                      "cursor-pointer outline-none transition-colors hover:bg-accent/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
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
                  {cell.iso && canManage && (
                    <Plus className="absolute top-1.5 right-1.5 size-3 text-muted-foreground opacity-0 transition-opacity group-hover/day:opacity-100" />
                  )}
                  <div className="mt-1 space-y-1">
                    {dayEvents.slice(0, 3).map((event) => {
                      const style =
                        TYPE_STYLES[event.event_type] ?? TYPE_STYLES.meeting;
                      return (
                        <button
                          key={event.id}
                          type="button"
                          onClick={(clicked) => {
                            clicked.stopPropagation();
                            openEvent(event);
                          }}
                          className={cn(
                            "block w-full truncate rounded px-1 py-0.5 text-left text-[10px] hover:brightness-95",
                            style.chip,
                            event.status === "tentative" && "opacity-60",
                            event.status === "cancelled" && "line-through opacity-50",
                          )}
                          title={event.title}
                        >
                          {event.title}
                        </button>
                      );
                    })}
                    {dayEvents.length > 3 && (
                      <p className="px-1 text-[10px] text-muted-foreground">
                        {t("body.calMore", { n: dayEvents.length - 3 })}
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
            <CardTitle className="text-sm">{t("body.calComingUp")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {upcoming.length === 0 ? (
              <EmptyState
                compact
                icon={CalendarDays}
                title={t("body.calendarEmptyTitle")}
                description={t("body.calendarEmptyDesc")}
                action={
                  canManage ? (
                    <Button size="sm" onClick={() => openNew(null)}>
                      <Plus className="size-4" />
                      {t("body.calNewEvent")}
                    </Button>
                  ) : null
                }
              />
            ) : (
              upcoming.map((event) => {
                const style =
                  TYPE_STYLES[event.event_type] ?? TYPE_STYLES.meeting;
                return (
                  <button
                    key={event.id}
                    type="button"
                    onClick={() => openEvent(event)}
                    className="flex w-full gap-2.5 rounded-md p-1 text-left transition-colors hover:bg-accent"
                  >
                    <span
                      className={cn(
                        "mt-1.5 size-2 shrink-0 rounded-full",
                        style.dot,
                      )}
                    />
                    <div className="min-w-0 flex-1">
                      <p
                        className={cn(
                          "truncate text-sm font-medium",
                          event.status === "cancelled" && "line-through",
                        )}
                      >
                        {event.title}
                      </p>
                      <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
                        <Clock className="size-3" />
                        {new Date(event.starts_at).toLocaleString(bcp, {
                          day: "numeric",
                          month: "short",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                        {event.status === "tentative" &&
                          ` · ${t("body.calTentative")}`}
                      </p>
                      {event.location && (
                        <p className="flex items-center gap-1 truncate text-[11px] text-muted-foreground">
                          <MapPin className="size-3 shrink-0" />
                          {event.location}
                        </p>
                      )}
                    </div>
                  </button>
                );
              })
            )}
          </CardContent>
        </Card>
      </div>

      {canManage && open && (
        <EventDialog
          // Remounts per target, which is how the form resets without an
          // effect writing over what is being typed.
          key={editing?.id ?? day ?? "new"}
          open
          onOpenChange={setOpen}
          event={editing}
          day={day}
          links={links}
        />
      )}
    </>
  );
}
