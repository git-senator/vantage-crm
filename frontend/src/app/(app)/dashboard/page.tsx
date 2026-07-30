import Link from "next/link";
import type { Metadata } from "next";
import {
  ArrowRight,
  CalendarDays,
  CircleDollarSign,
  Clock,
  Plus,
  Target,
  TrendingUp,
} from "lucide-react";

import {
  RevenueChart,
  type RevenuePoint,
} from "@/components/dashboard/revenue-chart";
import { GrowthIntelligence } from "@/components/dashboard/growth-intelligence";
import { EmptyState } from "@/components/shared/empty-state";
import { OwnerAvatar } from "@/components/shared/owner-avatar";
import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { formatPrice } from "@/lib/format";
import { getDashboardSummary } from "@/lib/api/dashboard";
import { listDeals } from "@/lib/api/deals";
import { listLeads } from "@/lib/api/leads";
import { getTaskQueue } from "@/lib/api/tasks";
import { listCalendarEvents } from "@/lib/api/calendar";
import { requireSession } from "@/lib/auth/session";
import type { SeriesPoint } from "@/lib/api/types";
import { getSeries } from "@/lib/api/analytics";
import { getTranslations } from "@/i18n/server";

export const metadata: Metadata = { title: "Dashboard" };

/**
 * Daily readings rolled up to months, for the revenue chart.
 *
 * Summed, not averaged: revenue_won is a *flow*, so a month is the total of
 * its days. Doing this to a level metric such as pipeline_open_value would
 * double-count every deal open on more than one day, which is exactly why the
 * metric registry declares which kind each one is.
 */
function monthlyRevenue(points: SeriesPoint[]): RevenuePoint[] {
  const months = new Map<string, number>();
  for (const point of points) {
    const key = point.date.slice(0, 7);
    months.set(key, (months.get(key) ?? 0) + (Number(point.value) || 0));
  }
  return [...months.entries()].slice(-7).map(([month, closed]) => ({
    month: new Date(month + "-01T00:00:00Z").toLocaleDateString(undefined, {
      month: "short",
      timeZone: "UTC",
    }),
    closed,
    // The chart carries a second series, and open pipeline is a level rather
    // than a flow — it has no monthly total to show. Left at zero rather than
    // invented from a number that does not mean what the axis implies.
    pipeline: 0,
  }));
}

/** 24-hour clock time, in the viewer's timezone. */
function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "just now", "3h ago", "2d ago", else a date. */
function relativeTime(iso: string): string {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export default async function DashboardPage() {
  const session = await requireSession();
  const t = await getTranslations();

  // Everything below reads live data within the caller's scope. Sorted here
  // rather than by the API because the list endpoint orders by created_at for
  // keyset pagination — a genuine "top N by value" needs a sorted aggregate
  // endpoint, which is Phase 4 reporting work. Over one page this is exact.
  const dayStart = new Date();
  dayStart.setHours(0, 0, 0, 0);
  const dayEnd = new Date(dayStart.getTime() + 86_400_000);

  const [summary, openDeals, hotLeadsPage, taskQueue, todayEvents] =
    await Promise.all([
      getDashboardSummary(),
      listDeals({ status: "open", limit: 50 }),
      listLeads({ temperature: "hot", limit: 5 }),
      getTaskQueue(5),
      listCalendarEvents({
        start: dayStart.toISOString(),
        end: dayEnd.toISOString(),
      }),
    ]);

  // Live since Phase 5.1. Snapshots for the history, today computed live —
  // the seam lives in the API, so this chart cannot go flat at midnight.
  const revenueSeries = await getSeries("revenue_won", 180);

  const topDeals = [...openDeals.data]
    .sort((a, b) => Number(b.value ?? 0) - Number(a.value ?? 0))
    .slice(0, 5);
  const hotLeads = hotLeadsPage.data.slice(0, 4);
  const openTasks = taskQueue.filter((t) => t.status !== "done").slice(0, 5);
  const recent = summary.recent_activity;
  // Today, in the viewer's day rather than a fixed date: the panel is "what is
  // on my plate now", which stops meaning that the moment it is hard-coded.
  const todaysEvents = todayEvents;

  const stats: Stat[] = [
    {
      label: t("body.dashOpenPipeline"),
      value: formatPrice(Number(summary.deals.open_value)),
      hint: t("body.dashOpenDealsCount", { n: summary.deals.open_count }),
      icon: CircleDollarSign,
    },
    {
      label: t("body.dashWeightedForecast"),
      value: formatPrice(Number(summary.deals.weighted_value)),
      hint: t("body.dashWonThisMonth", { n: summary.deals.won_this_month_count }),
      icon: TrendingUp,
    },
    {
      label: t("body.dashOpenLeads"),
      value: String(summary.leads.open),
      hint: t("body.dashLeadsTotal", { n: summary.leads.total }),
      icon: Target,
    },
    {
      label: t("body.dashOpenTasks"),
      value: String(summary.tasks.open),
      hint:
        summary.tasks.overdue > 0
          ? t("body.dashOverdue", { n: summary.tasks.overdue })
          : t("body.dashNoneOverdue"),
      icon: Clock,
      invertDelta: true,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.dashWelcome", { name: session.full_name.split(" ")[0] })}
        description={t("body.dashSubtitle")}
        actions={
          <>
            <Button variant="outline" render={<Link href="/reports" />}>
              <TrendingUp className="size-4" />
              {t("body.dashViewReports")}
            </Button>
            <Button render={<Link href="/deals/new" />}>
              <Plus className="size-4" />
              {t("body.newDeal")}
            </Button>
          </>
        }
      />

      <StatGrid stats={stats} />

      {/* ---------------------------------- growth intelligence (6.6) */}
      <GrowthIntelligence />

      <div className="grid gap-6 lg:grid-cols-3">
        {/* --------------------------------------------------- revenue */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>{t("body.dashRevenue")}</CardTitle>
            <CardDescription>{t("body.dashRevenueDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <RevenueChart
              data={monthlyRevenue(revenueSeries.points)}
              className="h-[280px] w-full"
            />
          </CardContent>
        </Card>

        {/* ---------------------------------------------------- agenda */}
        <Card>
          <CardHeader>
            <CardTitle>{t("body.dashAgenda")}</CardTitle>
            <CardAction>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={t("body.dashOpenCalendar")}
                render={<Link href="/calendar" />}
              >
                <CalendarDays className="size-4" />
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent className="space-y-3">
            {todaysEvents.map((event) => (
              <div
                key={event.id}
                className="flex gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/50"
              >
                <div className="tabular w-14 shrink-0 text-xs">
                  <p className="font-medium">{formatClock(event.starts_at)}</p>
                  <p className="text-muted-foreground">
                    {formatClock(event.ends_at)}
                  </p>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{event.title}</p>
                  {event.location && (
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {event.location}
                    </p>
                  )}
                  <p className="mt-1 text-xs text-muted-foreground">
                    {event.owner.full_name}
                    {event.attendees.length > 0 &&
                      ` + ${event.attendees.length}`}
                  </p>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ------------------------------------------------- top deals */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>{t("body.dashTopDeals")}</CardTitle>
            <CardDescription>{t("body.dashTopDealsDesc")}</CardDescription>
            <CardAction>
              <Button variant="ghost" size="sm" render={<Link href="/deals" />}>
                {t("body.dashAllDeals")}
                <ArrowRight className="size-4" />
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent className="space-y-1">
            {topDeals.map((deal, index) => (
              <div key={deal.id}>
                {index > 0 && <Separator className="my-1" />}
                <Link
                  href={`/deals/${deal.id}`}
                  className="flex items-center gap-4 rounded-lg p-2 transition-colors hover:bg-muted/50"
                >
                  {deal.owner ? (
                    <UserAvatar
                      user={{
                        id: deal.owner.id,
                        name: deal.owner.full_name,
                        initials: deal.owner.initials,
                        role: "",
                        hue: deal.owner.avatar_hue,
                      }}
                      size="md"
                    />
                  ) : null}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{deal.title}</p>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {deal.client.display_name}
                      {deal.expected_close_date
                        ? ` · closes ${deal.expected_close_date}`
                        : ""}
                    </p>
                  </div>
                  <div className="hidden w-32 shrink-0 sm:block">
                    <div className="mb-1 flex items-center justify-between text-xs">
                      <span className="text-muted-foreground">Confidence</span>
                      <span className="tabular font-medium">
                        {deal.probability}%
                      </span>
                    </div>
                    <Progress value={deal.probability} className="h-1.5" />
                  </div>
                  <div className="w-24 shrink-0 text-right">
                    <p className="tabular text-sm font-semibold">
                      {deal.value ? formatPrice(Number(deal.value)) : "—"}
                    </p>
                    <StatusBadge
                      status={deal.stage.key}
                      label={deal.stage.name}
                      className="mt-1"
                      dot={false}
                    />
                  </div>
                </Link>
              </div>
            ))}
          </CardContent>
        </Card>

        {/* -------------------------------------------------- hot leads */}
        <Card>
          <CardHeader>
            <CardTitle>{t("body.dashHotLeads")}</CardTitle>
            <CardDescription>{t("body.dashHotLeadsDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {hotLeads.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t("body.dashNoHotLeads")}
              </p>
            ) : (
              hotLeads.map((lead) => (
                <Link
                  key={lead.id}
                  href={`/leads/${lead.id}`}
                  className="flex items-center gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/50"
                >
                  <span className="tabular grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-sm font-semibold text-primary">
                    {lead.score ?? "—"}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {lead.full_name}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {[lead.preferred_location, lead.source]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </div>
                  <ArrowRight className="size-4 shrink-0 text-muted-foreground" />
                </Link>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ------------------------------------------------------ tasks */}
        <Card>
          <CardHeader>
            <CardTitle>{t("body.dashYourTasks")}</CardTitle>
            <CardAction>
              <Button variant="ghost" size="sm" render={<Link href="/tasks" />}>
                {t("body.dashAllTasks")}
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent className="space-y-2.5">
            {openTasks.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t("body.dashNothingDue")}
              </p>
            ) : (
              openTasks.map((task) => (
                <div key={task.id} className="flex items-start gap-3">
                  <span className="mt-1 size-4 shrink-0 rounded-[5px] border-2" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm">{task.title}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {task.due_at
                        ? `Due ${new Date(task.due_at).toLocaleDateString()}`
                        : "No due date"}
                      {task.is_overdue && (
                        <span className="ml-1.5 text-destructive">overdue</span>
                      )}
                    </p>
                  </div>
                  <StatusBadge status={task.priority} dot={false} />
                </div>
              ))
            )}
          </CardContent>
        </Card>

        {/* --------------------------------------------------- activity */}
        <Card>
          <CardHeader>
            <CardTitle>{t("body.dashRecentActivity")}</CardTitle>
            <CardDescription>{t("body.dashActivityDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            {recent.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t("body.dashNothingLogged")}
              </p>
            ) : (
              <ol className="space-y-4">
                {recent.slice(0, 6).map((item) => (
                  <li key={`${item.kind}-${item.id}`} className="flex gap-3">
                    <OwnerAvatar owner={item.actor} size="xs" className="mt-0.5" />
                    <div className="min-w-0 flex-1 text-sm">
                      <p className="leading-snug">
                        <span className="font-medium">
                          {item.actor?.full_name.split(" ")[0] ?? "System"}
                        </span>{" "}
                        <span className="text-muted-foreground">
                          {item.kind === "note" ? "noted" : item.type}
                        </span>{" "}
                        <span className="font-medium">
                          {item.title ?? ""}
                        </span>
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {relativeTime(item.timestamp)}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>

        {/* ------------------------------------- empty state demonstration */}
        <Card>
          <CardHeader>
            <CardTitle>{t("body.dashSaved")}</CardTitle>
            <CardDescription>{t("body.dashSavedDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <EmptyState
              compact
              icon={Target}
              title={t("body.dashSavedEmpty")}
              description={t("body.dashSavedEmptyDesc")}
              action={
                <Button variant="outline" size="sm">
                  <Plus className="size-4" />
                  {t("body.dashCreateSearch")}
                </Button>
              }
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
