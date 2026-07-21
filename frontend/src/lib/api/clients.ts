import "server-only";

import { apiFetch } from "@/lib/api/server";
import type { Client, ClientFilters, Page } from "@/lib/api/types";

/**
 * Server-side client queries, for React Server Components.
 *
 * Marked server-only: a client component importing this would ship the
 * internal API host and issue unauthenticated calls. Mutations go through the
 * companion module instead, which uses the same-origin BFF proxy.
 */

function toQuery(filters: ClientFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export async function listClients(
  filters: ClientFilters = {},
): Promise<Page<Client>> {
  return apiFetch<Page<Client>>(`/clients${toQuery(filters)}`);
}

export async function getClient(id: string): Promise<Client> {
  return apiFetch<Client>(`/clients/${id}`);
}

export async function getTypeCounts(): Promise<Record<string, number>> {
  return apiFetch<Record<string, number>>("/clients/stats/types");
}
