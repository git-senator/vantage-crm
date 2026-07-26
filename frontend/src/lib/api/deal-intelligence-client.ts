"use client";

import { apiRequest } from "@/lib/api/client";
import type { DealHealthDetail, DealInsightResponse } from "@/lib/api/types";

/**
 * Deal-intelligence reads from the browser, through the BFF proxy.
 *
 * `getHealth` is deterministic and free — it runs the rule engine and the
 * Analytics Engine's pipeline stats, no model call — so the panel fetches it on
 * mount. `getInsights` runs the AI narrative and costs, so it is behind a
 * button; a budget refusal returns 429 with a readable message.
 */

export async function getHealth(dealId: string): Promise<DealHealthDetail> {
  return apiRequest<DealHealthDetail>(`/ai/deals/${dealId}/health`);
}

export async function getInsights(
  dealId: string,
): Promise<DealInsightResponse> {
  return apiRequest<DealInsightResponse>(`/ai/deals/${dealId}/insights`);
}
