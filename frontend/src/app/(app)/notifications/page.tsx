import type { Metadata } from "next";

import { NotificationFeed } from "@/components/notifications/notification-feed";
import { NotificationPreferenceForm } from "@/components/notifications/preference-form";
import { PageHeader } from "@/components/shared/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  listNotificationPreferences,
  listNotifications,
} from "@/lib/api/notifications";

export const metadata: Metadata = { title: "Notifications" };

/**
 * Live since Phase 3.3 — the last fixture-backed page a user actually acts on.
 *
 * The list and the preferences are fetched in parallel: neither depends on the
 * other, and awaiting them in sequence would add a round trip for nothing.
 */
export default async function NotificationsPage() {
  const [notifications, preferences] = await Promise.all([
    listNotifications({ limit: 50 }),
    listNotificationPreferences(),
  ]);

  const unread = notifications.unread_count;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Notifications"
        description={
          unread === 0
            ? "You're all caught up."
            : `You have ${unread} unread update${unread === 1 ? "" : "s"}.`
        }
      />

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="min-w-0">
          <NotificationFeed notifications={notifications.data} />
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Delivery</CardTitle>
            </CardHeader>
            <CardContent>
              <NotificationPreferenceForm preferences={preferences} />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
