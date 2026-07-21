import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type {
  Deal,
  DealBoard,
  DealFilters,
  DealStageHistoryEntry,
  Page,
  Pipeline,
} from "@/lib/api/types";

/**
 * Server-side deal and pipeline queries, for React Server Components.
 *
 * Marked server-only: a client component importing this would ship the
 * internal API host and issue unauthenticated calls. Mutations go through the
 * companion module instead, which uses the same-origin BFF proxy.
 */

export async function listDeals(
  filters: DealFilters = {},
): Promise<Page<Deal>> {
  return apiFetch<Page<Deal>>(`/deals${toQuery(filters)}`);
}

export async function getDeal(id: string): Promise<Deal> {
  return apiFetch<Deal>(`/deals/${id}`);
}

export async function getDealHistory(
  id: string,
): Promise<DealStageHistoryEntry[]> {
  return apiFetch<DealStageHistoryEntry[]>(`/deals/${id}/history`);
}

/**
 * The Kanban board, grouped server-side.
 *
 * Grouping happens on the server so the column totals are computed against the
 * same scoped set the cards come from — summing client-side would silently
 * exclude anything beyond the board cap.
 */
export async function getDealBoard(
  params: { pipeline_id?: string; search?: string; owner_id?: string } = {},
): Promise<DealBoard> {
  return apiFetch<DealBoard>(`/deals/board${toQuery(params)}`);
}

export async function listPipelines(): Promise<Pipeline[]> {
  return apiFetch<Pipeline[]>("/pipelines");
}

export async function getPipeline(id: string): Promise<Pipeline> {
  return apiFetch<Pipeline>(`/pipelines/${id}`);
}
