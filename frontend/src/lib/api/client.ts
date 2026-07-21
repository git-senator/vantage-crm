"use client";

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

function readCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${CSRF_COOKIE}=`));
  return match ? decodeURIComponent(match.split("=")[1]) : null;
}

interface ClientRequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

export async function apiRequest<T>(
  path: string,
  { body, headers, method = "GET", ...init }: ClientRequestOptions = {},
): Promise<T> {
  const requestHeaders = new Headers(headers);
  requestHeaders.set("Accept", "application/json");

  if (body !== undefined) {
    requestHeaders.set("Content-Type", "application/json");
  }

  // Double-submit CSRF: the cookie is readable by JS on purpose, so we can
  // echo it in a header. A cross-site attacker can cause the cookie to be
  // sent but cannot read it to set the header (docs/SECURITY.md §2.5).
  const csrfToken = readCsrfToken();
  if (csrfToken && method !== "GET" && method !== "HEAD") {
    requestHeaders.set(CSRF_HEADER, csrfToken);
  }

  const response = await fetch(`/api${path}`, {
    ...init,
    method,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
    // Same-origin: the browser attaches the httpOnly session cookies.
    credentials: "same-origin",
  });

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
