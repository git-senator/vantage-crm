"use client";

import { useEffect } from "react";

import { refreshSession } from "@/lib/api/client";

/**
 * Keeps an open tab signed in.
 *
 * The access token expires every fifteen minutes. Renewal after the fact works
 * — a client call retries through `/api/auth/refresh`, a navigation through
 * `/api/auth/renew` — but both cost a round-trip the user waits for, and the
 * navigation one flickers through a redirect. Renewing a few minutes *before*
 * expiry means neither path is normally reached at all.
 *
 * Every renewal issues a fresh thirty-day refresh token, so a tab left open
 * stays signed in indefinitely. That is the intent: this is a CRM people keep
 * open all day, and being thrown back to the login screen mid-task is not a
 * security feature. Closing the browser still ends at the refresh token's own
 * thirty-day life, and `Sign out` still revokes immediately.
 */

/** Comfortably inside the fifteen-minute access-token life. */
const RENEW_EVERY_MS = 10 * 60 * 1000;

export function SessionKeepalive() {
  useEffect(() => {
    let lastRenewedAt = Date.now();

    const renew = () => {
      lastRenewedAt = Date.now();
      void refreshSession();
    };

    const timer = setInterval(renew, RENEW_EVERY_MS);

    // A background tab has its timers throttled, and a laptop that was asleep
    // ran none at all. Coming back into view is the moment the token is most
    // likely to be stale — and the moment before the user clicks something.
    const onVisibilityChange = () => {
      if (
        document.visibilityState === "visible" &&
        Date.now() - lastRenewedAt >= RENEW_EVERY_MS
      ) {
        renew();
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  return null;
}
