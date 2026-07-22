import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type { Task } from "@/lib/api/types";

/**
 * Server-side task reads. Phase 2.7 shipped the tasks backend; this exposes the
 * work-queue endpoint the dashboard needs (soonest due first, open only). The
 * full tasks page port is a later frontend slice.
 */
export async function getTaskQueue(limit = 5): Promise<Task[]> {
  return apiFetch<Task[]>(`/tasks/queue${toQuery({ limit })}`);
}
