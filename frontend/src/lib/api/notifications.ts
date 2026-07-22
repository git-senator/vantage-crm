import "server-only";

import { toQuery } from "@/lib/api/query";
import { apiFetch } from "@/lib/api/server";
import type {
  NotificationList,
  NotificationPreference,
  UnreadCount,
} from "@/lib/api/types";

/**
 * Notifications are scoped to the caller by the API — there is no user
 * parameter here and there is not meant to be. A notification belongs to its
 * recipient, and no role has a legitimate reason to read someone else's.
 */

export async function listNotifications(params?: {
  unread_only?: boolean;
  category?: string;
  limit?: number;
}): Promise<NotificationList> {
  return apiFetch<NotificationList>(`/notifications${toQuery(params ?? {})}`);
}

/**
 * Just the badge.
 *
 * The topbar needs one integer, and fetching the list to derive it would ship
 * every notification body to a component that renders none of them.
 */
export async function fetchUnreadCount(): Promise<number> {
  const { unread_count } = await apiFetch<UnreadCount>(
    "/notifications/unread-count",
  );
  return unread_count;
}

export async function listNotificationPreferences(): Promise<
  NotificationPreference[]
> {
  return apiFetch<NotificationPreference[]>("/notifications/preferences");
}
