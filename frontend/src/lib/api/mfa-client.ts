"use client";

import { apiRequest } from "@/lib/api/client";
import type {
  MfaEnrolmentStarted,
  MfaRecoveryCodes,
} from "@/lib/api/types";

/**
 * Enrolment is two calls on purpose: `begin` issues a secret, `activate`
 * proves a code from it. A one-step version would leave somebody locked out
 * with a secret they never successfully scanned.
 */
export async function beginMfaEnrolment(): Promise<MfaEnrolmentStarted> {
  return apiRequest<MfaEnrolmentStarted>("/auth/mfa/enroll", {
    method: "POST",
    body: {},
  });
}

export async function activateMfa(code: string): Promise<MfaRecoveryCodes> {
  return apiRequest<MfaRecoveryCodes>("/auth/mfa/activate", {
    method: "POST",
    body: { code },
  });
}

/** Requires the password: a session alone must not be able to remove a factor. */
export async function disableMfa(password: string): Promise<void> {
  await apiRequest("/auth/mfa/disable", { method: "POST", body: { password } });
}

export async function regenerateRecoveryCodes(
  password: string,
): Promise<MfaRecoveryCodes> {
  return apiRequest<MfaRecoveryCodes>("/auth/mfa/recovery-codes", {
    method: "POST",
    body: { password },
  });
}
