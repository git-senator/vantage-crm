"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Keeps a server-rendered view live without a manual reload.
 *
 * The inbox is server-rendered (selection is a search param, so a thread is
 * linkable and back-button-correct). That makes it static between navigations —
 * a reply that lands after the page rendered would not appear until the user
 * clicked away and back. This mounts a soft `router.refresh()` on an interval,
 * which re-runs the server component and pulls new messages in place. A soft
 * refresh preserves client state, so a half-typed reply in the composer is not
 * lost when the poll fires.
 */
export function AutoRefresh({ intervalMs = 4000 }: { intervalMs?: number }) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => router.refresh(), intervalMs);
    return () => clearInterval(id);
  }, [router, intervalMs]);
  return null;
}
