/**
 * Constants shared by server and client code.
 *
 * Deliberately free of secrets and of anything describing internal topology.
 * Cookie names and the CSRF header are needed on both sides — the browser must
 * read the CSRF cookie to echo it in a header (docs/SECURITY.md §2.5) — so
 * this module is safe to import from a client component.
 *
 * The internal API URL lives in `config.ts`, which is server-only.
 */

export const ACCESS_COOKIE = "vg_access";
export const REFRESH_COOKIE = "vg_refresh";
export const CSRF_COOKIE = "vg_csrf";

export const CSRF_HEADER = "X-CSRF-Token";

/** Path the BFF proxy exposes to the browser. */
export const BFF_PREFIX = "/api";
