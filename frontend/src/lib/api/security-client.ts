"use client";

import { apiRequest } from "@/lib/api/client";

/** Change the password. The server invalidates every session on success, so the
 *  caller must send the person back to sign in. */
export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await apiRequest("/auth/password/change", {
    method: "POST",
    body: { current_password: currentPassword, new_password: newPassword },
  });
}

/** Revoke every active session for the current user (sign out everywhere). */
export async function signOutEverywhere(): Promise<void> {
  await apiRequest("/enterprise/security-policy/revoke-sessions", {
    method: "POST",
    body: {},
  });
}
