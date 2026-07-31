import { type NextRequest, NextResponse } from "next/server";

import { API_INTERNAL_URL, API_PREFIX } from "@/lib/api/config";
import { BFF_PREFIX, CSRF_COOKIE, CSRF_HEADER } from "@/lib/api/constants";

/**
 * BFF proxy: browser -> Next.js -> FastAPI.
 *
 * One catch-all handler rather than a file per endpoint. The browser only ever
 * talks to this origin, which means no CORS, `SameSite=Strict` on the refresh
 * cookie, and the API stays unreachable from the internet
 * (docs/ARCHITECTURE.md §2, §3.2).
 *
 * Responsibilities, in order:
 *   1. verify CSRF on state-changing verbs
 *   2. forward the request with its cookies
 *   3. relay Set-Cookie back so rotation reaches the browser
 */

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * Headers that must not be forwarded.
 *
 * `host` would break virtual-host routing; the `content-length` and encoding
 * headers describe a body we may re-encode.
 */
const STRIPPED_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "content-length",
  "transfer-encoding",
  "accept-encoding",
]);

const STRIPPED_RESPONSE_HEADERS = new Set([
  "content-encoding",
  "content-length",
  "transfer-encoding",
  "connection",
]);

function csrfIsValid(request: NextRequest): boolean {
  if (!MUTATING_METHODS.has(request.method)) return true;

  const cookieToken = request.cookies.get(CSRF_COOKIE)?.value;
  const headerToken = request.headers.get(CSRF_HEADER);

  // Login and refresh establish the session and cannot present a token yet.
  // They are safe without one: login carries credentials, and the refresh
  // cookie is SameSite=Strict so a cross-site request cannot send it.
  const path = request.nextUrl.pathname;
  if (path.endsWith("/auth/login") || path.endsWith("/auth/refresh")) {
    return true;
  }

  // Public onboarding endpoints, called by a visitor with no session and so no
  // CSRF token to present — like login. They carry no session cookie to abuse,
  // which is what CSRF protects; both are rate-limited server-side instead.
  // Submit the request form, and redeem an invitation link.
  if (
    path.endsWith("/access-requests") ||
    (path.includes("/access-requests/invitations/") && path.endsWith("/accept"))
  ) {
    return true;
  }

  if (!cookieToken || !headerToken) return false;
  return cookieToken === headerToken;
}

/**
 * Rewrite a Set-Cookie Path from the API's URL space into the browser's.
 *
 * The API scopes the refresh cookie to its own path, `/api/v1/auth`. The
 * browser never sees that path — it talks to this proxy at `/api/auth`. Cookie
 * paths are matched by the browser against the URL *it* requests, so relaying
 * the header unchanged means the refresh cookie is never sent back and every
 * session dies at the 15-minute access-token expiry.
 *
 * Rewriting here rather than changing the API keeps the backend correct when
 * called directly (tests, service-to-service) while adapting it to the public
 * path this proxy actually exposes.
 */
function rewriteCookiePath(cookie: string): string {
  // Plain string replace rather than a regex: inside a template literal `\s`
  // collapses to `s`, so a naively built pattern silently never matches.
  return cookie.replace(`Path=${API_PREFIX}`, `Path=${BFF_PREFIX}`);
}

async function proxy(request: NextRequest): Promise<NextResponse> {
  if (!csrfIsValid(request)) {
    return NextResponse.json(
      {
        type: "https://vantage.crm/problems/csrf-failed",
        title: "CSRF validation failed",
        status: 403,
        detail: "Missing or invalid CSRF token.",
      },
      { status: 403, headers: { "content-type": "application/problem+json" } },
    );
  }

  // Everything after /api is the API path: /api/auth/login -> /api/v1/auth/login
  const upstreamPath = request.nextUrl.pathname.replace(/^\/api/, "");
  const target = `${API_INTERNAL_URL}${API_PREFIX}${upstreamPath}${request.nextUrl.search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!STRIPPED_REQUEST_HEADERS.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });

  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.text();

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    // The API is unreachable. Return a problem document rather than a raw 500
    // so the client error handling has one shape to deal with.
    return NextResponse.json(
      {
        type: "https://vantage.crm/problems/upstream-unavailable",
        title: "Service unavailable",
        status: 503,
        detail: "The API is not reachable. Please try again shortly.",
      },
      { status: 503, headers: { "content-type": "application/problem+json" } },
    );
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    // getSetCookie() handles multiple Set-Cookie headers; the plain iterator
    // would collapse them into one comma-joined value and break rotation.
    if (key.toLowerCase() === "set-cookie") return;
    if (!STRIPPED_RESPONSE_HEADERS.has(key.toLowerCase())) {
      responseHeaders.set(key, value);
    }
  });

  for (const cookie of upstream.headers.getSetCookie()) {
    responseHeaders.append("set-cookie", rewriteCookiePath(cookie));
  }

  return new NextResponse(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;

// Session responses must never be cached or statically optimised.
export const dynamic = "force-dynamic";
