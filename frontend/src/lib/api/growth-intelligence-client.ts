"use client";

import { apiRequest } from "@/lib/api/client";
import type {
  GrowthBriefingResponse,
  GrowthHealthDetail,
} from "@/lib/api/types";

/**
 * Growth-intelligence reads from the browser, through the BFF proxy.
 *
 * `getGrowth` is deterministic and free — it runs the rule engine over the
 * Analytics Engine's aggregates, no model call — so the panel fetches it on
 * mount. `getBriefing` runs a model and costs, so it is behind a button; a
 * budget refusal returns 429 with a readable message.
 */

export async function getGrowth(): Promise<GrowthHealthDetail> {
  return apiRequest<GrowthHealthDetail>(`/ai/growth`);
}

export async function getBriefing(): Promise<GrowthBriefingResponse> {
  return apiRequest<GrowthBriefingResponse>(`/ai/growth/briefing`);
}
