import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type { Page, Task, TaskFilters } from "@/lib/api/types";

/**
 * Server-side task reads, for React Server Components.
 *
 * `getTaskQueue` backs the dashboard panel (soonest due first, open only);
 * `listTasks` and `getTaskStatusCounts` back the Tasks page. Marked
 * server-only: mutations go through the companion `tasks-client` module over
 * the same-origin BFF proxy.
 */

export async function getTaskQueue(limit = 5): Promise<Task[]> {
  return apiFetch<Task[]>(`/tasks/queue${toQuery({ limit })}`);
}

export async function listTasks(
  filters: TaskFilters = {},
): Promise<Page<Task>> {
  return apiFetch<Page<Task>>(`/tasks${toQuery(filters)}`);
}

/** Open-task counts keyed by status — the board's column tallies and the
 * header stats, in one round trip. */
export async function getTaskStatusCounts(): Promise<Record<string, number>> {
  return apiFetch<Record<string, number>>("/tasks/stats/statuses");
}
