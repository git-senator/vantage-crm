"use client";

import { apiRequest } from "@/lib/api/client";
import type { Organization, WorkspaceSettings } from "@/lib/api/types";

/** Persist workspace settings — the name and/or the JSONB settings blob. */
export async function updateOrganization(input: {
  name?: string;
  settings?: WorkspaceSettings;
}): Promise<Organization> {
  return apiRequest<Organization>("/organizations/current", {
    method: "PATCH",
    body: input,
  });
}

/** Set a member's role to exactly `roleKey`.
 *
 * Roles are additive server-side, so switching means revoking whatever they
 * hold and granting the new one — done here so the caller sees a single "role"
 * selector, not the underlying set. `current` is what they hold now (skip the
 * revoke for anything they don't). */
export async function setMemberRole(
  userId: string,
  roleKey: string,
  current: string[],
): Promise<void> {
  for (const existing of current) {
    if (existing !== roleKey) {
      await apiRequest(`/roles/users/${userId}/revoke`, {
        method: "POST",
        body: { role_key: existing },
      });
    }
  }
  if (!current.includes(roleKey)) {
    await apiRequest(`/roles/users/${userId}/assign`, {
      method: "POST",
      body: { role_key: roleKey },
    });
  }
}
