import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type { TimelineItem } from "@/lib/api/types";

/**
 * Server-side timeline reads. The timeline is a merged, read-only view of
 * activities and notes; there are no timeline mutations (you edit the underlying
 * activity or note), so there is no client module.
 */

/** One record's merged timeline (activities + notes), newest first. */
export async function getEntityTimeline(
  entityType: string,
  entityId: string,
  limit = 50,
): Promise<TimelineItem[]> {
  return apiFetch<TimelineItem[]>(
    `/timeline${toQuery({ entity_type: entityType, entity_id: entityId, limit })}`,
  );
}

/** The cross-entity feed, within scope — backs the dashboard panel. */
export async function getTimelineFeed(limit = 50): Promise<TimelineItem[]> {
  return apiFetch<TimelineItem[]>(`/timeline${toQuery({ limit })}`);
}
