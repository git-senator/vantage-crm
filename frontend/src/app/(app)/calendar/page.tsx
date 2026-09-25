import type { Metadata } from "next";

import {
  CalendarBoard,
  type MonthCell,
} from "@/components/calendar/calendar-board";
import type { LinkOptions } from "@/components/calendar/event-dialog";
import { PageHeader } from "@/components/shared/page-header";
import { listCalendarEvents } from "@/lib/api/calendar";
import { listClients } from "@/lib/api/clients";
import { listDeals } from "@/lib/api/deals";
import { listLeads } from "@/lib/api/leads";
import { listProperties } from "@/lib/api/properties";
import { getLocale, getTranslations } from "@/i18n/server";
import { LOCALE_META } from "@/i18n/config";
import { hasPermission, requireSession } from "@/lib/auth/session";
import type { CalendarEvent } from "@/lib/api/types";

export const metadata: Metadata = { title: "Calendar" };

/**
 * A Monday-first grid covering the month, padded to whole weeks.
 *
 * Computed rather than hard-coded — the prototype pinned July 2026 because it
 * had no date engine; a live calendar has to answer for every month.
 */
function buildMonthGrid(year: number, month: number): MonthCell[] {
  const first = new Date(Date.UTC(year, month, 1));
  // getUTCDay is Sunday-first; shift so Monday is 0.
  const leading = (first.getUTCDay() + 6) % 7;
  const daysInMonth = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();

  const cells: MonthCell[] = [];
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
  return {
    start: start.toISOString(),
    end: end.toISOString(),
    // The API cancels rather than deletes so that "what was I meant to be
    // doing on Tuesday" still has an answer. Hiding cancelled events would
    // throw that answer away again and make a cancel look like a deletion,
    // so they stay in the grid, struck through.
    include_cancelled: true,
  };
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
 * linkable — the same choice the inbox and every detail view make. Everything
 * a person clicks lives in `CalendarBoard`, which this page hands the finished
 * grid to.
 */
export default async function CalendarPage({
  searchParams,
}: {
  searchParams: Promise<{ m?: string; new?: string }>;
}) {
  const { m, new: openNew } = await searchParams;
  const session = await requireSession();
  const t = await getTranslations();
  const bcp = LOCALE_META[await getLocale()].htmlLang;
  // Weekday headers and the month label come from Intl in the active locale, so
  // they read correctly in every language without a table of month names.
  const weekdayFmt = new Intl.DateTimeFormat(bcp, {
    weekday: "short",
    timeZone: "UTC",
  });
  // 2024-01-01 was a Monday; format seven consecutive days for a Monday-first row.
  const weekdays = Array.from({ length: 7 }, (_, i) =>
    weekdayFmt.format(new Date(Date.UTC(2024, 0, 1 + i))),
  );
  const now = new Date();
  const parsed = m?.match(/^(\d{4})-(\d{2})$/);
  const year = parsed ? Number(parsed[1]) : now.getFullYear();
  const month = parsed ? Number(parsed[2]) - 1 : now.getMonth();

  const canManage = hasPermission(session, "activities.manage");

  // The pickers offer only records the caller may reference — each list is
  // scoped server-side. Fetched alongside the events rather than on demand,
  // because a dialog that opens with an empty dropdown reads as broken.
  const [events, leads, clients, deals, properties] = await Promise.all([
    listCalendarEvents(monthWindow(year, month)),
    canManage ? listLeads({ limit: 100 }) : null,
    canManage ? listClients({ limit: 100 }) : null,
    canManage ? listDeals({ limit: 100 }) : null,
    canManage ? listProperties({ limit: 100 }) : null,
  ]);

  const links: LinkOptions = {
    lead: (leads?.data ?? []).map((lead) => ({
      id: lead.id,
      label: `${lead.first_name} ${lead.last_name}`.trim(),
    })),
    client: (clients?.data ?? []).map((client) => ({
      id: client.id,
      label: client.display_name,
    })),
    deal: (deals?.data ?? []).map((deal) => ({
      id: deal.id,
      label: deal.title,
    })),
    property: (properties?.data ?? []).map((property) => ({
      id: property.id,
      label: property.title,
    })),
  };

  const byDay: Record<string, CalendarEvent[]> = {};
  for (const event of events) {
    const key = localDateKey(event.starts_at);
    byDay[key] = [...(byDay[key] ?? []), event];
  }

  const cells = buildMonthGrid(year, month);
  const monthLabel = new Date(Date.UTC(year, month, 1)).toLocaleDateString(bcp, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
  const previous = new Date(Date.UTC(year, month - 1, 1));
  const next = new Date(Date.UTC(year, month + 1, 1));
  const href = (date: Date) =>
    `/calendar?m=${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;

  // Cancelled events stay in the grid as a record, but they are not plans, so
  // they do not belong in a list headed "coming up".
  const upcoming = events
    .filter(
      (event) =>
        event.status !== "cancelled" && new Date(event.starts_at) >= now,
    )
    .slice(0, 8);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.calendarTitle")}
        description={t("body.calendarDesc")}
      />

      <CalendarBoard
        cells={cells}
        weekdays={weekdays}
        monthLabel={monthLabel}
        previousHref={href(previous)}
        nextHref={href(next)}
        byDay={byDay}
        upcoming={upcoming}
        todayKey={localDateKey(now.toISOString())}
        bcp={bcp}
        canManage={canManage}
        links={links}
        openOnArrival={openNew === "1"}
      />
    </div>
  );
}
