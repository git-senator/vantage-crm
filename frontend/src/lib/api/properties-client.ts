"use client";

import { apiRequest } from "@/lib/api/client";
import type { Property, PropertyInput } from "@/lib/api/types";

/**
 * Property mutations from the browser.
 *
 * Goes through the same-origin BFF proxy, which attaches the session cookie
 * and verifies CSRF. Nothing here handles a token.
 *
 * Note that update and delete can return 403 rather than 404 for a listing the
 * caller can see but does not own — listings are shared inventory, so the
 * record's existence is not a secret. Callers should surface the server's
 * message rather than assuming "not found".
 */

export async function createProperty(input: PropertyInput): Promise<Property> {
  return apiRequest<Property>("/properties", { method: "POST", body: input });
}

export async function updateProperty(
  id: string,
  input: Partial<PropertyInput>,
): Promise<Property> {
  return apiRequest<Property>(`/properties/${id}`, {
    method: "PATCH",
    body: input,
  });
}

export async function deleteProperty(id: string): Promise<void> {
  return apiRequest<void>(`/properties/${id}`, { method: "DELETE" });
}

export async function assignProperty(
  id: string,
  listingAgentId: string,
): Promise<Property> {
  return apiRequest<Property>(`/properties/${id}/assign`, {
    method: "POST",
    body: { listing_agent_id: listingAgentId },
  });
}
