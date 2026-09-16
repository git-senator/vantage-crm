import "server-only";

import { API_PREFIX } from "@/lib/api/config";
import { BFF_PREFIX } from "@/lib/api/constants";

/**
 * Rewrite a Set-Cookie Path from the API's URL space into the browser's.
 *
 * The API scopes the refresh cookie to its own path, `/api/v1/auth`. The
 * browser never sees that path — it talks to the BFF proxy at `/api/auth`.
 * Cookie paths are matched by the browser against the URL *it* requests, so
 * relaying the header unchanged means the refresh cookie is never sent back and
 * every session dies at the 15-minute access-token expiry.
 *
 * Rewriting here rather than changing the API keeps the backend correct when
 * called directly (tests, service-to-service) while adapting it to the public
 * path the proxy actually exposes.
 *
 * Shared by the catch-all proxy and the renewal route so the two cannot drift:
 * a path rewritten in one and not the other is a silent logout.
 */
export function rewriteCookiePath(cookie: string): string {
  // Plain string replace rather than a regex: inside a template literal `\s`
  // collapses to `s`, so a naively built pattern silently never matches.
  return cookie.replace(`Path=${API_PREFIX}`, `Path=${BFF_PREFIX}`);
}
