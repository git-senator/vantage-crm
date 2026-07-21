"use client";

import { apiRequest } from "@/lib/api/client";
import type { Lead, LeadInput } from "@/lib/api/types";

/**
 * Lead mutations from the browser.
 *
 * Goes through the same-origin BFF proxy, which attaches the session cookie
 * and verifies CSRF. Nothing here handles a token.
 */

export async function createLead(input: LeadInput): Promise<Lead> {
  return apiRequest<Lead>("/leads", { method: "POST", body: input });
}

export async function updateLead(
  id: string,
  input: Partial<LeadInput>,
): Promise<Lead> {
  return apiRequest<Lead>(`/leads/${id}`, { method: "PATCH", body: input });
}

export async function deleteLead(id: string): Promise<void> {
  return apiRequest<void>(`/leads/${id}`, { method: "DELETE" });
}

export async function assignLead(id: string, ownerId: string): Promise<Lead> {
  return apiRequest<Lead>(`/leads/${id}/assign`, {
    method: "POST",
    body: { owner_id: ownerId },
  });
}
