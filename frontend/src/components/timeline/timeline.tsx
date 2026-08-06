import {
  ArrowRightLeft,
  CalendarClock,
  Home,
  Mail,
  Phone,
  StickyNote,
  Users,
  type LucideIcon,
} from "lucide-react";

import { OwnerAvatar } from "@/components/shared/owner-avatar";
import { Badge } from "@/components/ui/badge";
import type { TimelineItem } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const ICONS: Record<string, LucideIcon> = {
  call: Phone,
  email: Mail,
  meeting: Users,
  showing: Home,
  note: StickyNote,
  stage_change: ArrowRightLeft,
};

/** "just now", "3h ago", "2d ago", or a date once it is old enough. */
function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

/**
 * The merged activity + note timeline, presentational. Data comes from the
 * server (`/timeline?entity_type=&entity_id=`); this only renders it, so it can
 * live in a server component and stays free of client state.
 */
export function Timeline({
  items,
  emptyLabel = "Nothing logged yet.",
}: {
  items: TimelineItem[];
  emptyLabel?: string;
}) {
  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground">{emptyLabel}</p>;
  }

  return (
    <ol className="space-y-0">
      {items.map((item, index) => {
        const Icon = ICONS[item.type] ?? CalendarClock;
        const last = index === items.length - 1;
        return (
          <li key={`${item.kind}-${item.id}`} className="relative flex gap-3 pb-5">
            {!last && (
              <span
                aria-hidden
                className="absolute left-[15px] top-8 bottom-0 w-px bg-border"
              />
            )}
            <span
              className={cn(
                "grid size-8 shrink-0 place-items-center rounded-full border bg-card",
                item.is_system && "border-primary/30 text-primary",
              )}
            >
              <Icon className="size-4" />
            </span>

            <div className="min-w-0 flex-1 pt-1">
              <div className="flex items-baseline justify-between gap-2">
                <p className="text-sm font-medium">
                  {item.title ?? (item.kind === "note" ? "Note" : "Activity")}
                  {item.is_pinned && (
                    <Badge variant="secondary" className="ml-2 align-middle">
                      Pinned
                    </Badge>
                  )}
                </p>
                <time
                  className="shrink-0 text-xs text-muted-foreground"
                  dateTime={item.timestamp}
                  title={new Date(item.timestamp).toLocaleString()}
                >
                  {relativeTime(item.timestamp)}
                </time>
              </div>

              {item.body && (
                <p className="mt-0.5 line-clamp-3 text-sm text-muted-foreground whitespace-pre-wrap">
                  {item.body}
                </p>
              )}

              <div className="mt-1.5 flex items-center gap-1.5">
                <OwnerAvatar owner={item.actor} size="xs" />
                <span className="text-xs text-muted-foreground">
                  {item.actor?.full_name ?? "System"}
                </span>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
