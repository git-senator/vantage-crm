"use client";

import { apiRequest } from "@/lib/api/client";
import type { LeadInsightResponse, LeadScoreDetail } from "@/lib/api/types";

/**
 * Lead-intelligence reads from the browser, through the BFF proxy.
 *
 * `getScore` is deterministic and free — no model call — so the panel fetches it
 * on mount. `getInsights` runs the AI narrative and costs, so it is behind an
 * explicit button; a budget refusal returns 429 with a readable message.
 */

export async function getScore(leadId: string): Promise<LeadScoreDetail> {
  return apiRequest<LeadScoreDetail>(`/ai/leads/${leadId}/score`);
}

export async function getInsights(
  leadId: string,
): Promise<LeadInsightResponse> {
  return apiRequest<LeadInsightResponse>(`/ai/leads/${leadId}/insights`);
}
