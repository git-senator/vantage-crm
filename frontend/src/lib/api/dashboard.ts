import "server-only";

import { apiFetch } from "@/lib/api/server";
import type { DashboardSummary } from "@/lib/api/types";

/**
 * The dashboard summary — counts, pipeline value and recent activity, all
 * computed server-side within the caller's scope. Read-only; there is nothing
 * to mutate.
 */
export async function getDashboardSummary(): Promise<DashboardSummary> {
  return apiFetch<DashboardSummary>("/dashboard/summary");
}
