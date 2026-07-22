"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { markConversationRead } from "@/lib/api/conversations-client";

/**
 * Marks a thread read once it is on screen.
 *
 * Renders nothing. Opening a conversation *is* reading it, so an explicit
 * button would be asking the user to tell us something we already know — and
 * read state here is per workspace, so leaving it unset means a colleague sees
 * a thread as unread that somebody has already dealt with.
 *
 * Guarded by a ref rather than a dependency array: React runs effects twice in
 * development, and firing two POSTs to say the same thing is noise.
 */
export function MarkReadOnView({
  conversationId,
  unreadCount,
}: {
  conversationId: string;
  unreadCount: number;
}) {
  const router = useRouter();
  const claimed = useRef<string | null>(null);

  useEffect(() => {
    if (unreadCount === 0) return;
    if (claimed.current === conversationId) return;
    claimed.current = conversationId;

    void markConversationRead(conversationId)
      .then(() => router.refresh())
      // A failure here costs an incorrect badge, not data. It must not surface
      // as an error over the thread the user came to read.
      .catch(() => undefined);
  }, [conversationId, unreadCount, router]);

  return null;
}
