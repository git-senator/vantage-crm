"use client";

import { LOCALE_COOKIE } from "@/i18n/config";
import { CSRF_COOKIE, CSRF_HEADER } from "@/lib/api/constants";
import type { ProblemDetail } from "@/lib/api/types";

/**
 * Browser-side API client.
 *
 * Calls the same-origin BFF proxy, never the API directly. Tokens live in
 * httpOnly cookies the browser attaches automatically — nothing here reads or
 * stores a token, so an XSS cannot exfiltrate a session.
 */

export class ClientApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: ProblemDetail | null,
    message: string,
  ) {
    super(message);
    this.name = "ClientApiError";
  }

  /** Field-level validation messages, keyed by field name. */
  get fieldErrors(): Record<string, string> {
    const errors = this.problem?.errors ?? [];
    return Object.fromEntries(errors.map((e) => [e.field, e.message]));
  }
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.split("=")[1]) : null;
}

function readCsrfToken(): string | null {
  return readCookie(CSRF_COOKIE);
}

interface ClientRequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

/** Paths that must not trigger a refresh-retry: they own the session
 *  lifecycle, and retrying /auth/refresh would recurse. */
const NO_REFRESH_PATHS = ["/auth/login", "/auth/refresh", "/auth/logout"];

//: A single in-flight refresh shared across concurrent 401s, so a page that
//: fires several requests at once renews the session once, not once per call.
let refreshInFlight: Promise<boolean> | null = null;

/**
 * Exchange the month-long refresh cookie for a fresh 15-minute access token.
 *
 * The refresh cookie is path-scoped to `/api/auth`, so only a call to
 * `/api/auth/refresh` carries it — which is why renewal has to happen here on
 * the client rather than in the generic proxy. On success the browser stores
 * the rotated cookies and the original request can simply be replayed.
 */
function refreshSession(): Promise<boolean> {
  refreshInFlight ??= fetch(`/api/auth/refresh`, {
    method: "POST",
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  })
    .then((response) => response.ok)
    .catch(() => false)
    .finally(() => {
      refreshInFlight = null;
    });
  return refreshInFlight;
}

function sendOnce(
  path: string,
  { body, headers, method = "GET", ...init }: ClientRequestOptions,
): Promise<Response> {
  const requestHeaders = new Headers(headers);
  requestHeaders.set("Accept", "application/json");

  if (body !== undefined) {
    requestHeaders.set("Content-Type", "application/json");
  }

  // Double-submit CSRF: the cookie is readable by JS on purpose, so we can
  // echo it in a header. A cross-site attacker can cause the cookie to be
  // sent but cannot read it to set the header (docs/SECURITY.md §2.5). Read
  // fresh on every send so a replay after a refresh picks up a rotated token.
  const csrfToken = readCsrfToken();
  if (csrfToken && method !== "GET" && method !== "HEAD") {
    requestHeaders.set(CSRF_HEADER, csrfToken);
  }

  // The app's own language, not the browser's. `Accept-Language` would
  // otherwise carry whatever the operating system is set to, and a Brazilian
  // agent who switched the CRM to Russian would keep receiving Portuguese
  // listing text — the toggle would move the labels and nothing else.
  const locale = readCookie(LOCALE_COOKIE);
  if (locale) {
    requestHeaders.set("Accept-Language", locale);
  }

  return fetch(`/api${path}`, {
    ...init,
    method,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
    // Same-origin: the browser attaches the httpOnly session cookies.
    credentials: "same-origin",
  });
}

export async function apiRequest<T>(
  path: string,
  options: ClientRequestOptions = {},
): Promise<T> {
  let response = await sendOnce(path, options);

  // Transparent session renewal: the access token expired, but the refresh
  // cookie may still renew it. Refresh once and replay, so an agent working
  // through the day is not logged out every 15 minutes.
  if (
    response.status === 401 &&
    !NO_REFRESH_PATHS.some((p) => path.startsWith(p))
  ) {
    if (await refreshSession()) {
      response = await sendOnce(path, options);
    }
  }

  if (!response.ok) {
    let problem: ProblemDetail | null = null;
    try {
      problem = (await response.json()) as ProblemDetail;
    } catch {
      // Non-JSON response (gateway error). Fall through to a generic message.
    }
    throw new ClientApiError(
      response.status,
      problem,
      problem?.detail ?? "Something went wrong. Please try again.",
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
