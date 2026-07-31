import { type NextRequest, NextResponse } from "next/server";

import { ACCESS_COOKIE, REFRESH_COOKIE } from "@/lib/api/constants";

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
 * before a redirect, and to keep signed-in users off the login page.
 */

/** Reachable without a session. */
const PUBLIC_PATHS = ["/login", "/request-access", "/accept-invite"];

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  );
}

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  const hasAccess = request.cookies.has(ACCESS_COOKIE);
  const hasRefresh = request.cookies.has(REFRESH_COOKIE);
  // A live refresh cookie means the session can be renewed even though the
  // 15-minute access token has expired. Treating that as signed-out would log
  // people out every quarter hour.
  const looksAuthenticated = hasAccess || hasRefresh;

  if (isPublic(pathname)) {
    if (looksAuthenticated) {
      return NextResponse.redirect(new URL("/dashboard", request.url));
    }
    return NextResponse.next();
  }

  if (!looksAuthenticated) {
    const loginUrl = new URL("/login", request.url);
    // Preserve the destination so login can return the user to it. Only the
    // path is carried — an absolute URL here would be an open-redirect.
    if (pathname !== "/") {
      loginUrl.searchParams.set("next", `${pathname}${search}`);
    }
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  /*
   * Everything except:
   *   api        — the BFF proxy handles its own auth
   *   _next/*    — build output
   *   static assets
   */
  matcher: [
    "/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
