import "server-only";

import { cookies } from "next/headers";

import { API_INTERNAL_URL, API_PREFIX } from "@/lib/api/config";
import { ACCESS_COOKIE } from "@/lib/api/constants";
import type { ProblemDetail } from "@/lib/api/types";
import { LOCALE_COOKIE } from "@/i18n/config";

/**
 * Server-side API client.
 *
 * Used by React Server Components and route handlers. The call goes
 * Next.js server -> FastAPI over the internal network; the browser is not
 * involved, so there is no CORS, no preflight, and the access token is never
 * exposed to JavaScript.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: ProblemDetail | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** True when the session is absent or expired — the caller should redirect. */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Forward the caller's access token. Defaults to true. */
  authenticated?: boolean;
}

export async function apiFetch<T>(
  path: string,
  { body, authenticated = true, headers, ...init }: RequestOptions = {},
): Promise<T> {
  const requestHeaders = new Headers(headers);
  requestHeaders.set("Accept", "application/json");

  if (body !== undefined) {
    requestHeaders.set("Content-Type", "application/json");
  }

  // Content follows the toggle, not just the chrome. The API answers with a
  // listing's text in the language asked for here, so a reader who switched to
  // Portuguese gets a Portuguese description rather than Portuguese labels
  // wrapped around Russian prose. Set unless the caller already chose one.
  if (!requestHeaders.has("Accept-Language")) {
    const locale = (await cookies()).get(LOCALE_COOKIE)?.value;
    if (locale) {
      requestHeaders.set("Accept-Language", locale);
    }
  }

  if (authenticated) {
    const token = (await cookies()).get(ACCESS_COOKIE)?.value;
    if (token) {
      // Bearer rather than forwarding the cookie: the API is a separate origin
      // on the internal network and has no reason to see our cookie jar.
      requestHeaders.set("Authorization", `Bearer ${token}`);
    }
  }

  const response = await fetch(`${API_INTERNAL_URL}${API_PREFIX}${path}`, {
    ...init,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
    // Session data must never be cached across requests or users.
    cache: "no-store",
  });

  if (!response.ok) {
    let problem: ProblemDetail | null = null;
    try {
      problem = (await response.json()) as ProblemDetail;
    } catch {
      // Non-JSON error (gateway, timeout). Fall through with a generic message.
    }
    throw new ApiError(
      response.status,
      problem,
      problem?.detail ?? `Request failed with status ${response.status}`,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
