import "server-only";

import { cache } from "react";
import { redirect } from "next/navigation";

import { ApiError, apiFetch } from "@/lib/api/server";
import type { UserProfile } from "@/lib/api/types";

/**
 * Server-side session access.
 *
 * `getSession` is wrapped in React's `cache`, so a single render that calls it
 * from the layout, the page and three components issues one request rather
 * than five. Deduplication is per-request, so nothing leaks between users.
 */

export const getSession = cache(async (): Promise<UserProfile | null> => {
  try {
    return await apiFetch<UserProfile>("/auth/me");
  } catch (error) {
    if (error instanceof ApiError && error.isUnauthenticated) {
      return null;
    }
    // A backend outage is not the same as being signed out. Rethrowing lets
    // the error boundary show "something went wrong" instead of silently
    // bouncing the user to the login page, which would look like their session
    // had expired.
    throw error;
  }
});

/**
 * Session or redirect. Use in any authenticated server component.
 *
 * Middleware already blocks unauthenticated navigation; this is the second
 * layer. Middleware only sees cookie presence, not validity — a forged or
 * expired token gets past it and is caught here.
 */
export async function requireSession(): Promise<UserProfile> {
  const session = await getSession();
  if (!session) {
    redirect("/login");
  }
  return session;
}

/**
 * Whether the session holds a permission.
 *
 * For hiding UI the user cannot act on. **This is UX, not a control.** Every
 * action is authorized server-side regardless of what the client renders —
 * see docs/SECURITY.md §1.4.
 */
export function hasPermission(
  session: UserProfile | null,
  permission: string,
): boolean {
  return session?.permissions.includes(permission) ?? false;
}

export function hasAnyPermission(
  session: UserProfile | null,
  permissions: readonly string[],
): boolean {
  if (permissions.length === 0) return true;
  return permissions.some((permission) => hasPermission(session, permission));
}
