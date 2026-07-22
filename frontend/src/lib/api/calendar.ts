import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type { CalendarEvent } from "@/lib/api/types";

/**
 * Events overlapping a window. The window is required — a calendar without
 * bounds is a full-table scan dressed as a feature, and there is no sensible
 * default because a month view, a day view and a record's schedule panel all
 * mean something different by "now".
 */
export async function listCalendarEvents(params: {
  start: string;
  end: string;
  owner_id?: string;
  entity_type?: string;
  entity_id?: string;
  event_type?: string;
  include_cancelled?: boolean;
}): Promise<CalendarEvent[]> {
  return apiFetch<CalendarEvent[]>(`/calendar${toQuery(params)}`);
}
