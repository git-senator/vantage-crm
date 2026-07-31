"use client";

import { apiRequest } from "@/lib/api/client";

/**
 * Access-request and invitation calls, through the same-origin BFF proxy.
 *
 * The submit and accept endpoints are public — a visitor with no session uses
 * them — so they carry no CSRF token; the proxy allowlists exactly those two
 * paths. The review calls are authenticated and CSRF-protected like any other.
 */

export interface AccessRequestInput {
  full_name: string;
  email: string;
  requested_role?: string | null;
  message?: string | null;
}

export interface AccessRequest {
  id: string;
  full_name: string;
  email: string;
  requested_role: string | null;
  message: string | null;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  reviewed_at: string | null;
}

export interface InvitationInfo {
  email: string;
  full_name: string;
  organization_name: string;
}

export type AssignableRole = "admin" | "manager" | "agent";

interface MessageResponse {
  message: string;
}

// ------------------------------------------------------------------ public

export async function submitAccessRequest(
  input: AccessRequestInput,
): Promise<MessageResponse> {
  return apiRequest<MessageResponse>("/access-requests", {
    method: "POST",
    body: input,
  });
}

export async function fetchInvitation(token: string): Promise<InvitationInfo> {
  return apiRequest<InvitationInfo>(
    `/access-requests/invitations/${encodeURIComponent(token)}`,
  );
}

export async function acceptInvitation(
  token: string,
  password: string,
): Promise<MessageResponse> {
  return apiRequest<MessageResponse>(
    `/access-requests/invitations/${encodeURIComponent(token)}/accept`,
    { method: "POST", body: { password } },
  );
}

// ------------------------------------------------------------- review queue

export async function listAccessRequests(): Promise<AccessRequest[]> {
  return apiRequest<AccessRequest[]>("/access-requests");
}

export async function approveAccessRequest(
  id: string,
  roleKey: AssignableRole,
): Promise<AccessRequest> {
  return apiRequest<AccessRequest>(`/access-requests/${id}/approve`, {
    method: "POST",
    body: { role_key: roleKey },
  });
}

export async function rejectAccessRequest(id: string): Promise<AccessRequest> {
  return apiRequest<AccessRequest>(`/access-requests/${id}/reject`, {
    method: "POST",
    body: {},
  });
}
