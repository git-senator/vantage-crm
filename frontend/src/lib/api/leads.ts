import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type { Lead, LeadFilters, Page } from "@/lib/api/types";

/**
 * Server-side lead queries, for React Server Components.
 *
 * Marked server-only: a client component importing this would ship the
 * internal API host and issue unauthenticated calls. Mutations go through the
 * client module instead, which uses the same-origin BFF proxy.
 */

export async function listLeads(filters: LeadFilters = {}): Promise<Page<Lead>> {
  return apiFetch<Page<Lead>>(`/leads${toQuery(filters)}`);
}

export async function getLead(id: string): Promise<Lead> {
  return apiFetch<Lead>(`/leads/${id}`);
}

export async function getStageCounts(): Promise<Record<string, number>> {
  return apiFetch<Record<string, number>>("/leads/stats/stages");
}
