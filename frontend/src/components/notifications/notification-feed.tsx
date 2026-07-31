"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  AtSign,
  Bell,
  CheckCheck,
  CircleDollarSign,
  FileText,
  ListChecks,
  Target,
} from "lucide-react";

import { EmptyState } from "@/components/shared/empty-state";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useTranslation } from "@/i18n/language-provider";
import { LOCALE_META } from "@/i18n/config";
import type { Locale } from "@/i18n/config";
import { ClientApiError } from "@/lib/api/client";
import {
  markAllNotificationsRead,
  markNotificationRead,
} from "@/lib/api/notifications-client";
import type { Notification, NotificationCategory } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export const CATEGORY_META: Record<
  NotificationCategory,
  { icon: typeof Bell; className: string; label: string }
> = {
  deal: {
    icon: CircleDollarSign,
    className: "bg-success/12 text-success",
    label: "Deals",
  },
  lead: { icon: Target, className: "bg-primary/10 text-primary", label: "Leads" },
  task: { icon: ListChecks, className: "bg-info/12 text-info", label: "Tasks" },
  document: {
    icon: FileText,
    className: "bg-muted text-muted-foreground",
    label: "Documents",
  },
  mention: {
    icon: AtSign,
    className:
      "bg-warning/18 text-warning-foreground dark:bg-warning/20 dark:text-warning",
    label: "Mentions",
  },
  system: { icon: Bell, className: "bg-muted text-muted-foreground", label: "System" },
};

/**
 * Relative time, localized via Intl so plurals and wording are correct in every
 * language. `justNow` is the one phrase Intl renders awkwardly ("this minute"),
 * so it is passed in from a translation.
 */
function relativeTime(iso: string, locale: Locale, justNow: string): string {
  const bcp = LOCALE_META[locale].htmlLang;
  const then = new Date(iso).getTime();
  const minutes = Math.round((Date.now() - then) / 60000);
  const rtf = new Intl.RelativeTimeFormat(bcp, { numeric: "auto" });
  if (minutes < 1) return justNow;
  if (minutes < 60) return rtf.format(-minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (hours < 24) return rtf.format(-hours, "hour");
  const days = Math.round(hours / 24);
  if (days < 30) return rtf.format(-days, "day");
  return new Date(iso).toLocaleDateString(bcp);
}

/**
 * The notification list.
 *
 * Reading is a mutation — it changes the badge everyone else in the app sees —
 * so this is a client component over server-fetched data, and it refreshes the
 * route after each change rather than holding a second copy of the truth.
 */
export function NotificationFeed({
  notifications,
}: {
  notifications: Notification[];
}) {
  const router = useRouter();
  const { t } = useTranslation();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const unread = notifications.filter((n) => n.read_at === null);
  const earlier = notifications.filter((n) => n.read_at !== null);

  async function run(action: () => Promise<unknown>) {
    setPending(true);
    setError(null);
    try {
      await action();
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : t("body.genericError"),
      );
    } finally {
      setPending(false);
    }
  }

  if (notifications.length === 0) {
    return (
      <EmptyState
        compact
        icon={CheckCheck}
        title={t("body.notifCaughtUp")}
        description={t("body.notifCaughtUpDesc")}
      />
    );
  }

  return (
    <div className="space-y-4">
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {unread.length > 0 && (
        <div className="flex justify-end">
          <Button
            variant="outline"
            size="sm"
            disabled={pending}
            onClick={() => run(markAllNotificationsRead)}
          >
            <CheckCheck className="size-4" />
            {t("body.notifMarkAll")}
          </Button>
        </div>
      )}

      <NotificationGroup
        title={t("body.notifNew")}
        items={unread}
        pending={pending}
        onRead={(id) => run(() => markNotificationRead(id))}
      />
      <NotificationGroup
        title={t("body.notifEarlier")}
        items={earlier}
        pending={pending}
      />
    </div>
  );
}

function NotificationGroup({
  title,
  items,
  pending,
  onRead,
}: {
  title: string;
  items: Notification[];
  pending: boolean;
  onRead?: (id: string) => void;
}) {
  const { t, locale } = useTranslation();
  if (items.length === 0) return null;

  return (
    <section className="space-y-2">
      <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {title}
      </h2>
      <Card className="gap-0 overflow-hidden p-0">
        {items.map((item, index) => {
          const meta = CATEGORY_META[item.category] ?? CATEGORY_META.system;
          return (
            <div
              key={item.id}
              className={cn(
                "flex gap-3 p-4 transition-colors hover:bg-muted/50",
                index > 0 && "border-t",
                item.read_at === null && "bg-accent/30",
              )}
            >
              <span
                className={cn(
                  "grid size-9 shrink-0 place-items-center rounded-lg",
                  meta.className,
                )}
              >
                <meta.icon className="size-4" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{item.title}</p>
                {item.body && (
                  <p className="mt-0.5 text-sm text-muted-foreground">
                    {item.body}
                  </p>
                )}
                <p
                  className="mt-1 text-[11px] text-muted-foreground"
                  suppressHydrationWarning
                >
                  {relativeTime(item.created_at, locale, t("body.notifJustNow"))}
                  {item.actor && ` · ${item.actor.full_name}`}
                </p>
              </div>
              {onRead && (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={pending}
                  onClick={() => onRead(item.id)}
                >
                  {t("body.notifMarkRead")}
                </Button>
              )}
            </div>
          );
        })}
      </Card>
    </section>
  );
}
