import "server-only";

import { apiFetch } from "@/lib/api/server";
import type { Lead, LeadFilters, Page } from "@/lib/api/types";

/**
 * Server-side lead queries, for React Server Components.
 *
 * Marked server-only: a client component importing this would ship the
 * internal API host and issue unauthenticated calls. Mutations go through the
 * client module instead, which uses the same-origin BFF proxy.
 */

function toQuery(filters: LeadFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export async function listLeads(filters: LeadFilters = {}): Promise<Page<Lead>> {
  return apiFetch<Page<Lead>>(`/leads${toQuery(filters)}`);
}

export async function getLead(id: string): Promise<Lead> {
  return apiFetch<Lead>(`/leads/${id}`);
}

export async function getStageCounts(): Promise<Record<string, number>> {
  return apiFetch<Record<string, number>>("/leads/stats/stages");
}
