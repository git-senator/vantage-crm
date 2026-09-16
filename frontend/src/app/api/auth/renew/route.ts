import { type NextRequest, NextResponse } from "next/server";

import { API_INTERNAL_URL, API_PREFIX } from "@/lib/api/config";
import { ACCESS_COOKIE } from "@/lib/api/constants";
import { rewriteCookiePath } from "@/lib/api/session-cookies";

/**
 * Silent session renewal for a page navigation.
 *
 * The access token lives fifteen minutes; the session behind it lives thirty
 * days. Client-side calls bridge that gap themselves — a 401 triggers
 * `/api/auth/refresh` and the request is replayed. **A navigation cannot.** A
 * server component renders with whatever cookies arrived and has no way to set
 * new ones, so an expired access token meant `requireSession` bounced the user
 * to the login screen even though their session was perfectly alive. Leave a
 * tab for a quarter of an hour, click anything, and you were signed out.
 *
 * Middleware is the only place in a navigation that *can* set cookies, but it
 * cannot renew by itself: the refresh cookie is scoped to `/api/auth` and is
 * therefore not attached to a request for `/dashboard`. So middleware redirects
 * here instead — a URL under that path, which the browser does send the cookie
 * with — and this route rotates the session and sends the user on to where they
 * were going. Two extra round-trips, only on the navigation after expiry.
 *
 * On failure it hands over to the login page rather than clearing the session:
 * `SameSite=Strict` keeps the refresh cookie off cross-site requests, so a
 * third-party page loading this URL sees the refresh fail — and must not be
 * able to use that to sign the user out.
 */

/** Where to send the user afterwards. Only same-site paths — an absolute URL
 *  here, or a protocol-relative one, would make this an open redirect. */
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/")) return "/dashboard";
  if (raw.startsWith("//") || raw.startsWith("/\\")) return "/dashboard";
  return raw;
}

function toLogin(request: NextRequest, next: string): NextResponse {
  const url = new URL("/login", request.url);
  if (next !== "/" && next !== "/dashboard") {
    url.searchParams.set("next", next);
  }
  return NextResponse.redirect(url);
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const next = safeNext(request.nextUrl.searchParams.get("next"));

  let upstream: Response;
  try {
    upstream = await fetch(`${API_INTERNAL_URL}${API_PREFIX}/auth/refresh`, {
      method: "POST",
      headers: {
        // The refresh cookie as the browser sent it. Nothing else is needed:
        // `/auth/refresh` authenticates on the token alone.
        cookie: request.headers.get("cookie") ?? "",
        accept: "application/json",
      },
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    // The API is down. Not the same as being signed out, but there is nothing
    // to render either; the login page is the one screen that needs no session.
    return toLogin(request, next);
  }

  const cookies = upstream.ok ? upstream.headers.getSetCookie() : [];
  // Trust the Set-Cookie, not the status: a 200 that somehow carried no new
  // access cookie would send the user back to middleware, which would redirect
  // here again, and around forever.
  const renewed = cookies.some((cookie) =>
    new RegExp(`^${ACCESS_COOKIE}=[^;]`).test(cookie),
  );
  if (!renewed) {
    return toLogin(request, next);
  }

  const response = NextResponse.redirect(new URL(next, request.url));
  for (const cookie of cookies) {
    response.headers.append("set-cookie", rewriteCookiePath(cookie));
  }
  // The redirect itself must never be cached: it carries a rotated session.
  response.headers.set("Cache-Control", "no-store");
  return response;
}

export const dynamic = "force-dynamic";
