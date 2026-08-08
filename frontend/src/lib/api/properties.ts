import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type {
  Page,
  Property,
  PropertyFilters,
  PropertyPhoto,
} from "@/lib/api/types";

/**
 * Server-side property queries, for React Server Components.
 *
 * Marked server-only: a client component importing this would ship the
 * internal API host and issue unauthenticated calls. Mutations go through the
 * companion module instead, which uses the same-origin BFF proxy.
 */

export async function listProperties(
  filters: PropertyFilters = {},
): Promise<Page<Property>> {
  return apiFetch<Page<Property>>(`/properties${toQuery(filters)}`);
}

export async function getProperty(id: string): Promise<Property> {
  return apiFetch<Property>(`/properties/${id}`);
}

/**
 * The listing's gallery, cover first.
 *
 * Fetched on the server so the page arrives with its photos already in the
 * markup — the URLs are short-lived, and a client-side round trip would leave
 * the gallery blank for the first moment of every visit.
 */
export async function getPropertyPhotos(id: string): Promise<PropertyPhoto[]> {
  return apiFetch<PropertyPhoto[]>(`/properties/${id}/photos`);
}

export async function getStatusCounts(): Promise<Record<string, number>> {
  return apiFetch<Record<string, number>>("/properties/stats/statuses");
}
