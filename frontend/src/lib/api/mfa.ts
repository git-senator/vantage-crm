import "server-only";

import { apiFetch } from "@/lib/api/server";
import type { MfaStatus } from "@/lib/api/types";

/**
 * Whether MFA is on for the caller, and whether their roles oblige it.
 *
 * `setup_required` is computed server-side so the required-role list lives in
 * one place rather than being duplicated into the frontend where it can drift.
 */
export async function getMfaStatus(): Promise<MfaStatus> {
  return apiFetch<MfaStatus>("/auth/mfa");
}
