import { type NextRequest, NextResponse } from "next/server";

import { ACCESS_COOKIE, CSRF_COOKIE } from "@/lib/api/constants";

/**
 * Route protection at the edge.
 *
 * This is a **navigation guard, not an authorization control**. Middleware
 * only sees whether a cookie is present — it does not verify the signature,
 * because the JWT secret has no business in the edge runtime. A forged or
 * expired token gets past this and is rejected by the API, which is the layer
 * that actually decides (docs/SECURITY.md §1.4).
 *
 * Its job is to spare an unauthenticated visitor a flash of the app shell
 * before a redirect, to keep signed-in users off the login page, and to send a
 * navigation whose access token has expired through renewal instead of through
 * the login screen.
 */

/** Reachable without a session. */
const PUBLIC_PATHS = ["/login", "/request-access", "/accept-invite"];

/** Renews the session and forwards the user on — see that route's comment. */
const RENEW_PATH = "/api/auth/renew";

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  );
}

/**
 * Where the user was heading, with Next's own cache-busting parameter dropped:
 * a client-side navigation appends `_rsc`, and carrying it through the renewal
 * redirect would strand it in the address bar.
 */
function destination(request: NextRequest): string {
  const url = request.nextUrl.clone();
  url.searchParams.delete("_rsc");
  return `${url.pathname}${url.search}`;
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const hasAccess = request.cookies.has(ACCESS_COOKIE);
  /*
   * Whether there is a session worth trying to renew.
   *
   * Not the refresh cookie: it is scoped to `/api/auth`, so the browser does
   * not attach it to a request for `/dashboard` and looking for it here always
   * found nothing. The CSRF cookie is set at the same moment, lives exactly as
   * long as the refresh token, and is sent on every path — which makes it the
   * one signal available here that a session exists. It decides nothing on its
   * own; `/api/auth/renew` presents the real token and the API rules on it.
   */
  const renewable = request.cookies.has(CSRF_COOKIE);

  if (isPublic(pathname)) {
    // Only a live access cookie counts as signed in here. Bouncing a merely
    // renewable visitor to /dashboard would send them straight back to renewal,
    // and if that session is in fact dead, back here again.
    if (hasAccess) {
      return NextResponse.redirect(new URL("/dashboard", request.url));
    }
    return NextResponse.next();
  }

  if (hasAccess) {
    return NextResponse.next();
  }

  if (renewable) {
    // The fifteen-minute access token expired while the session did not. Renew
    // and come back, rather than treating it as a sign-out.
    const renew = new URL(RENEW_PATH, request.url);
    renew.searchParams.set("next", destination(request));
    return NextResponse.redirect(renew);
  }

  const loginUrl = new URL("/login", request.url);
  // Preserve the destination so login can return the user to it. Only the
  // path is carried — an absolute URL here would be an open-redirect.
  if (pathname !== "/") {
    loginUrl.searchParams.set("next", destination(request));
  }
  return NextResponse.redirect(loginUrl);
}

export const config = {
  /*
   * Everything except:
   *   api        — the BFF proxy and the renewal route handle their own auth
   *   _next/*    — build output
   *   static assets
   */
  matcher: [
    "/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
