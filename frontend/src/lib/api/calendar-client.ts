"use client";

import { apiRequest } from "@/lib/api/client";
import type {
  CalendarEvent,
  CalendarEventInput,
  CalendarEventSaved,
} from "@/lib/api/types";

/**
 * Calendar mutations from the browser, through the same-origin BFF proxy.
 *
 * Creating and updating return the event **and anything it collides with**.
 * The server reports overlaps rather than refusing them — a broker covering
 * two open houses on the same street is doing it on purpose — so the caller
 * has to show the conflicts instead of assuming a save was uneventful.
 */

export async function createEvent(
  input: CalendarEventInput,
): Promise<CalendarEventSaved> {
  return apiRequest<CalendarEventSaved>("/calendar", {
    method: "POST",
    body: input,
  });
}

export async function updateEvent(
  id: string,
  input: Partial<CalendarEventInput>,
): Promise<CalendarEventSaved> {
  return apiRequest<CalendarEventSaved>(`/calendar/${id}`, {
    method: "PATCH",
    body: input,
  });
}

/**
 * Cancel, which is what the API wants you to reach for: "what was I meant to
 * be doing on Tuesday" is a real question, and a deleted event takes its
 * answer with it. The event stays, struck through.
 */
export async function cancelEvent(id: string): Promise<CalendarEvent> {
  return apiRequest<CalendarEvent>(`/calendar/${id}/cancel`, { method: "POST" });
}

/** Removes it for good. Offered only after a cancel, never as the first move. */
export async function deleteEvent(id: string): Promise<void> {
  return apiRequest<void>(`/calendar/${id}`, { method: "DELETE" });
}
