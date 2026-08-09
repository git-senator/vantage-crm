"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";

import { apiRequest } from "@/lib/api/client";
import type { AttentionCounts } from "@/lib/api/types";

/** Nothing is waiting. Also what a failed request falls back to. */
const QUIET: AttentionCounts = {
  messages: 0,
  tasks: 0,
  requests: 0,
  notifications: 0,
};

/** How often the sidebar asks again while the tab is open. */
const POLL_MS = 60_000;

/**
 * What is waiting on the signed-in person, kept roughly current.
 *
 * Polled rather than pushed: a dot that is up to a minute stale costs nothing,
 * and a socket held open for four integers costs a connection per tab.
 *
 * It also refetches whenever the route changes, which is what makes the dot
 * feel immediate in the case that matters — you answer the last unread thread,
 * navigate away, and the dot is already gone.
 *
 * **A failed request goes quiet rather than sticking.** The sidebar is
 * chrome; if the API is unreachable the user has larger problems than a dot,
 * and a stale dot that cannot be cleared is worse than no dot at all.
 */
export function useAttention(): AttentionCounts {
  const [counts, setCounts] = useState<AttentionCounts>(QUIET);
  const pathname = usePathname();

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const next = await apiRequest<AttentionCounts>("/attention");
        if (!cancelled) setCounts(next);
      } catch {
        if (!cancelled) setCounts(QUIET);
      }
    };

    void load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [pathname]);

  return counts;
}
