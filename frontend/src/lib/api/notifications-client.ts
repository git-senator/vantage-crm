"use client";

import { apiRequest } from "@/lib/api/client";
import type {
  Notification,
  NotificationCategory,
  NotificationPreference,
  UnreadCount,
} from "@/lib/api/types";

export async function markNotificationRead(id: string): Promise<Notification> {
  return apiRequest<Notification>(`/notifications/${id}/read`, {
    method: "POST",
    body: {},
  });
}

export async function markAllNotificationsRead(): Promise<UnreadCount> {
  return apiRequest<UnreadCount>("/notifications/read-all", {
    method: "POST",
    body: {},
  });
}

/**
 * PUT the whole set, not one switch.
 *
 * Mirrors the API deliberately: a per-category PATCH would let two open
 * preference screens silently overwrite each other a switch at a time.
 */
export async function saveNotificationPreferences(
  preferences: Array<{
    category: NotificationCategory;
    in_app: boolean;
    email: boolean;
  }>,
): Promise<NotificationPreference[]> {
  return apiRequest<NotificationPreference[]>("/notifications/preferences", {
    method: "PUT",
    body: { preferences },
  });
}
