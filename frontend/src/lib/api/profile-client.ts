"use client";

import { apiRequest } from "@/lib/api/client";
import type { ProfileUpdateInput, UserProfile } from "@/lib/api/types";

/**
 * Update the signed-in user's own profile, through the BFF proxy (session
 * cookie + CSRF attached there). Returns the fresh profile.
 */
export async function updateProfile(
  input: ProfileUpdateInput,
): Promise<UserProfile> {
  return apiRequest<UserProfile>("/auth/me", { method: "PATCH", body: input });
}
