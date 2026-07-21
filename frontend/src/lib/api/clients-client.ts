"use client";

import { apiRequest } from "@/lib/api/client";
import type { Client, ClientInput, ConvertLeadInput } from "@/lib/api/types";

/**
 * Client mutations from the browser.
 *
 * Goes through the same-origin BFF proxy, which attaches the session cookie
 * and verifies CSRF. Nothing here handles a token.
 *
 * The filename is unfortunate next to `client.ts` (the HTTP client), but it
 * follows the `<entity>-client.ts` convention `leads-client.ts` established —
 * consistency beats a one-off exception.
 */

export async function createClient(input: ClientInput): Promise<Client> {
  return apiRequest<Client>("/clients", { method: "POST", body: input });
}

export async function updateClient(
  id: string,
  input: Partial<ClientInput>,
): Promise<Client> {
  return apiRequest<Client>(`/clients/${id}`, { method: "PATCH", body: input });
}

export async function deleteClient(id: string): Promise<void> {
  return apiRequest<void>(`/clients/${id}`, { method: "DELETE" });
}

export async function assignClient(
  id: string,
  ownerId: string,
): Promise<Client> {
  return apiRequest<Client>(`/clients/${id}/assign`, {
    method: "POST",
    body: { owner_id: ownerId },
  });
}

/**
 * Convert a lead into a client. Returns the new client.
 *
 * Posted to the leads collection because the action starts from a lead. A
 * second call for the same lead returns 409 — conversion is one-shot.
 */
export async function convertLead(
  leadId: string,
  input: ConvertLeadInput = {},
): Promise<Client> {
  return apiRequest<Client>(`/leads/${leadId}/convert`, {
    method: "POST",
    body: input,
  });
}
