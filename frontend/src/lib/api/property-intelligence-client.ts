"use client";

import { apiRequest } from "@/lib/api/client";
import type {
  PropertyContentKind,
  PropertyContentResponse,
  PropertyQualityDetail,
} from "@/lib/api/types";

/**
 * Property-intelligence reads from the browser, through the BFF proxy.
 *
 * `getQuality` is deterministic and free — it runs the rule engine and the
 * Analytics Engine's market stats, no model call — so the panel fetches it on
 * mount. `generateContent` runs a model and costs, so it is behind a button; a
 * budget refusal returns 429 with a readable message.
 */

export async function getQuality(
  propertyId: string,
): Promise<PropertyQualityDetail> {
  return apiRequest<PropertyQualityDetail>(
    `/ai/properties/${propertyId}/quality`,
  );
}

export async function generateContent(
  propertyId: string,
  kind: PropertyContentKind,
): Promise<PropertyContentResponse> {
  return apiRequest<PropertyContentResponse>(
    `/ai/properties/${propertyId}/content?kind=${kind}`,
  );
}
