import type { Metadata } from "next";
import {
  AtSign,
  Bell,
  CheckCheck,
  CircleDollarSign,
  ListChecks,
  Settings2,
  Target,
} from "lucide-react";

import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { notifications } from "@/lib/mock-data";
import { cn } from "@/lib/utils";
import type { Notification } from "@/types";

export const metadata: Metadata = { title: "Notifications" };

const categoryMeta: Record<
  Notification["category"],
  { icon: typeof Bell; className: string; label: string }
> = {
  deal: { icon: CircleDollarSign, className: "bg-success/12 text-success", label: "Deals" },
  lead: { icon: Target, className: "bg-primary/10 text-primary", label: "Leads" },
  task: { icon: ListChecks, className: "bg-info/12 text-info", label: "Tasks" },
  mention: { icon: AtSign, className: "bg-warning/18 text-warning-foreground dark:bg-warning/20 dark:text-warning", label: "Mentions" },
  system: { icon: Bell, className: "bg-muted text-muted-foreground", label: "System" },
};

const deliveryPreferences = [
  { label: "New lead assigned to me", email: true, push: true },
  { label: "Deal stage changes", email: true, push: false },
  { label: "Task due within 24 hours", email: false, push: true },
  { label: "Mentions and comments", email: true, push: true },
  { label: "Weekly performance digest", email: true, push: false },
];

export default function NotificationsPage() {
  const unread = notifications.filter((n) => !n.read);
  const earlier = notifications.filter((n) => n.read);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Notifications"
        description={`You have ${unread.length} unread updates.`}
        actions={
          <>
            <Button variant="outline">
              <CheckCheck className="size-4" />
              Mark all read
            </Button>
            <Button variant="outline">
              <Settings2 className="size-4" />
              Preferences
            </Button>
          </>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="min-w-0 space-y-4">
          <Tabs defaultValue="all">
            <TabsList>
              <TabsTrigger value="all">All</TabsTrigger>
              <TabsTrigger value="unread" className="gap-1.5">
                Unread
                <span className="tabular rounded-full bg-primary px-1.5 text-[11px] text-primary-foreground">
                  {unread.length}
                </span>
              </TabsTrigger>
              <TabsTrigger value="mentions">Mentions</TabsTrigger>
              <TabsTrigger value="archived">Archived</TabsTrigger>
            </TabsList>
          </Tabs>

          <NotificationGroup title="New" items={unread} />
          <NotificationGroup title="Earlier" items={earlier} />

          <EmptyState
            compact
            icon={CheckCheck}
            title="You're all caught up"
            description="Anything older than 30 days is moved to the archive automatically."
          />
        </div>

        {/* ------------------------------------------------- preferences */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Filter by type</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1">
              {Object.entries(categoryMeta).map(([key, meta]) => {
                const count = notifications.filter(
                  (n) => n.category === key,
                ).length;
                return (
                  <button
                    key={key}
                    className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-sm transition-colors hover:bg-muted"
                  >
                    <span
                      className={cn(
                        "grid size-7 shrink-0 place-items-center rounded-md",
                        meta.className,
                      )}
                    >
                      <meta.icon className="size-3.5" />
                    </span>
                    <span className="flex-1">{meta.label}</span>
                    <span className="tabular text-xs text-muted-foreground">
                      {count}
                    </span>
                  </button>
                );
              })}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Delivery</CardTitle>
            </CardHeader>
            <CardContent className="space-y-0">
              <div className="flex items-center justify-end gap-6 pb-2 text-[11px] text-muted-foreground">
                <span className="w-8 text-center">Email</span>
                <span className="w-8 text-center">Push</span>
              </div>
              {deliveryPreferences.map((pref, index) => (
                <div
                  key={pref.label}
                  className={cn(
                    "flex items-center gap-3 py-3",
                    index > 0 && "border-t",
                  )}
                >
                  <Label className="min-w-0 flex-1 text-sm font-normal">
                    {pref.label}
                  </Label>
                  <span className="flex w-8 justify-center">
                    <Switch defaultChecked={pref.email} aria-label={`Email: ${pref.label}`} />
                  </span>
                  <span className="flex w-8 justify-center">
                    <Switch defaultChecked={pref.push} aria-label={`Push: ${pref.label}`} />
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function NotificationGroup({
  title,
  items,
}: {
  title: string;
  items: Notification[];
}) {
  if (items.length === 0) return null;

  return (
    <section className="space-y-2">
      <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {title}
      </h2>
      <Card className="gap-0 overflow-hidden p-0">
        {items.map((item, index) => {
          const meta = categoryMeta[item.category];
          return (
            <div
              key={item.id}
              className={cn(
                "flex gap-3 p-4 transition-colors hover:bg-muted/50",
                index > 0 && "border-t",
                !item.read && "bg-accent/30",
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
                <div className="flex items-start gap-2">
                  <p className="min-w-0 flex-1 text-sm font-medium">
                    {item.title}
                  </p>
                  {!item.read && (
                    <span className="mt-1.5 size-2 shrink-0 rounded-full bg-primary" />
                  )}
                </div>
                <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                  {item.body}
                </p>
                <p className="mt-1.5 text-xs text-muted-foreground">
                  {item.timestamp}
                </p>
              </div>
            </div>
          );
        })}
      </Card>
    </section>
  );
}
