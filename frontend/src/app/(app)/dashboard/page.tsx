import Link from "next/link";
import type { Metadata } from "next";
import {
  ArrowRight,
  CalendarDays,
  CircleDollarSign,
  Clock,
  Handshake,
  Plus,
  Sparkles,
  Target,
  TrendingUp,
} from "lucide-react";

import { RevenueChart } from "@/components/dashboard/revenue-chart";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { StatusBadge } from "@/components/shared/status-badge";
import { AvatarStack, UserAvatar } from "@/components/shared/user-avatar";
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
import { listDeals } from "@/lib/api/deals";
import {
  activity,
  calendarEvents,
  currentUser,
  leads,
  revenueByMonth,
  tasks,
} from "@/lib/mock-data";

export const metadata: Metadata = { title: "Dashboard" };

const stats: Stat[] = [
  {
    label: "Pipeline value",
    value: "$19.5M",
    delta: 12.4,
    hint: "vs. last month",
    icon: CircleDollarSign,
  },
  {
    label: "Active deals",
    value: "10",
    delta: 8.1,
    hint: "3 closing this month",
    icon: Handshake,
  },
  {
    label: "New leads",
    value: "48",
    delta: 22.7,
    hint: "this week",
    icon: Target,
  },
  {
    label: "Avg. days to close",
    value: "38",
    delta: -6.2,
    hint: "faster than Q1",
    icon: Clock,
    invertDelta: true,
  },
];

const aiInsights = [
  {
    tone: "urgent" as const,
    title: "Harper Lindqvist is going cold",
    body: "Score dropped 8 points after four days without contact. She toured twice in one week — worth a call today.",
  },
  {
    tone: "opportunity" as const,
    title: "Three Folsom comps just closed above ask",
    body: "The 2201 Folsom listing may be underpriced by roughly 6%. Consider revisiting before it goes live Thursday.",
  },
  {
    tone: "risk" as const,
    title: "88 Townsend contingency expires in 6 days",
    body: "The disclosure packet is still blocked on HOA documents. Sofia flagged this three hours ago.",
  },
];

const toneStyles = {
  urgent: "border-l-destructive",
  opportunity: "border-l-success",
  risk: "border-l-warning",
};

export default async function DashboardPage() {
  const todaysEvents = calendarEvents.filter((e) => e.date === "2026-07-20");
  const openTasks = tasks.filter((t) => t.status !== "done").slice(0, 5);
  // Real deals now. Sorted here rather than by the API because the list
  // endpoint orders by created_at for keyset pagination — a genuine "top N by
  // value" needs a sorted, aggregate endpoint, which is Phase 4 reporting
  // work. Over one page this is exact; past it, it is the top of a page.
  const openDeals = await listDeals({ status: "open", limit: 50 });
  const topDeals = [...openDeals.data]
    .sort((a, b) => Number(b.value ?? 0) - Number(a.value ?? 0))
    .slice(0, 5);
  const hotLeads = leads.filter((l) => l.temperature === "hot").slice(0, 4);

  return (
    <div className="space-y-6">
      <PageHeader
        title={`Good morning, ${currentUser.name.split(" ")[0]}`}
        description="Here's where your book of business stands on Monday, July 20."
        actions={
          <>
            <Button variant="outline" render={<Link href="/reports" />}>
              <TrendingUp className="size-4" />
              View reports
            </Button>
            <Button>
              <Plus className="size-4" />
              New deal
            </Button>
          </>
        }
      />

      <StatGrid stats={stats} />

      {/* ------------------------------------------------ AI insight strip */}
      <Card className="gap-0 overflow-hidden py-0">
        <CardHeader className="border-b py-4">
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="size-4 text-primary" />
            What needs your attention
          </CardTitle>
          <CardDescription>
            Generated from pipeline activity over the last 7 days.
          </CardDescription>
          <CardAction>
            <Button variant="ghost" size="sm" render={<Link href="/ai-assistant" />}>
              Ask a follow-up
              <ArrowRight className="size-4" />
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className="grid gap-px bg-border p-0 md:grid-cols-3">
          {aiInsights.map((insight) => (
            <div
              key={insight.title}
              className={`border-l-2 bg-card p-5 ${toneStyles[insight.tone]}`}
            >
              <p className="text-sm font-medium">{insight.title}</p>
              <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                {insight.body}
              </p>
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* --------------------------------------------------- revenue */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Revenue performance</CardTitle>
            <CardDescription>
              Closed volume against open pipeline, in millions.
            </CardDescription>
            <CardAction>
              <Button variant="outline" size="sm">
                Last 7 months
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent>
            <RevenueChart data={revenueByMonth} className="h-[280px] w-full" />
          </CardContent>
        </Card>

        {/* ---------------------------------------------------- agenda */}
        <Card>
          <CardHeader>
            <CardTitle>Today&apos;s agenda</CardTitle>
            <CardDescription>Monday, July 20</CardDescription>
            <CardAction>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Open calendar"
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
                  <p className="font-medium">{event.start}</p>
                  <p className="text-muted-foreground">{event.end}</p>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{event.title}</p>
                  {event.location && (
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {event.location}
                    </p>
                  )}
                  <div className="mt-2">
                    <AvatarStack users={event.attendees} max={3} />
                  </div>
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
            <CardTitle>Deals closest to closing</CardTitle>
            <CardDescription>Ranked by contract value.</CardDescription>
            <CardAction>
              <Button variant="ghost" size="sm" render={<Link href="/deals" />}>
                All deals
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
            <CardTitle>Hot leads</CardTitle>
            <CardDescription>Scored 80 and above.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {hotLeads.map((lead) => (
              <Link
                key={lead.id}
                href="/leads"
                className="flex items-center gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/50"
              >
                <span className="tabular grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-sm font-semibold text-primary">
                  {lead.score}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{lead.name}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {lead.location} · {lead.source}
                  </p>
                </div>
                <ArrowRight className="size-4 shrink-0 text-muted-foreground" />
              </Link>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ------------------------------------------------------ tasks */}
        <Card>
          <CardHeader>
            <CardTitle>Your open tasks</CardTitle>
            <CardAction>
              <Button variant="ghost" size="sm" render={<Link href="/tasks" />}>
                All tasks
              </Button>
            </CardAction>
          </CardHeader>
          <CardContent className="space-y-2.5">
            {openTasks.map((task) => (
              <div key={task.id} className="flex items-start gap-3">
                <span className="mt-1 size-4 shrink-0 rounded-[5px] border-2" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{task.title}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {task.dueDate}
                  </p>
                </div>
                <StatusBadge status={task.priority} dot={false} />
              </div>
            ))}
          </CardContent>
        </Card>

        {/* --------------------------------------------------- activity */}
        <Card>
          <CardHeader>
            <CardTitle>Team activity</CardTitle>
          </CardHeader>
          <CardContent>
            <ol className="space-y-4">
              {activity.slice(0, 5).map((item) => (
                <li key={item.id} className="flex gap-3">
                  <UserAvatar user={item.actor} size="xs" className="mt-0.5" />
                  <div className="min-w-0 flex-1 text-sm">
                    <p className="leading-snug">
                      <span className="font-medium">
                        {item.actor.name.split(" ")[0]}
                      </span>{" "}
                      <span className="text-muted-foreground">
                        {item.action}
                      </span>{" "}
                      <span className="font-medium">{item.target}</span>
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {item.timestamp}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>

        {/* ------------------------------------- empty state demonstration */}
        <Card>
          <CardHeader>
            <CardTitle>Saved searches</CardTitle>
            <CardDescription>
              Pin a filtered view to reach it in one click.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <EmptyState
              compact
              icon={Target}
              title="No saved searches yet"
              description="Save a filter from any list view and it will show up here."
              action={
                <Button variant="outline" size="sm">
                  <Plus className="size-4" />
                  Create a search
                </Button>
              }
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
