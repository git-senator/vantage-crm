import type { Metadata } from "next";
import {
  CircleDollarSign,
  GripVertical,
  Handshake,
  Percent,
  Plus,
  Table2,
  Target,
} from "lucide-react";

import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatPrice } from "@/lib/format";
import { deals } from "@/lib/mock-data";
import type { DealStage } from "@/types";

export const metadata: Metadata = { title: "Deals" };

const stats: Stat[] = [
  { label: "Open pipeline", value: "$17.0M", delta: 12.4, hint: "9 active deals", icon: CircleDollarSign },
  { label: "Weighted forecast", value: "$9.8M", delta: 7.9, hint: "probability-adjusted", icon: Target },
  { label: "Projected commission", value: "$419K", delta: 10.2, hint: "next 90 days", icon: Handshake },
  { label: "Win rate", value: "34%", delta: 2.8, hint: "trailing 12 months", icon: Percent },
];

const columns: { stage: DealStage; label: string; accent: string }[] = [
  { stage: "qualification", label: "Qualification", accent: "bg-muted-foreground/40" },
  { stage: "showing", label: "Showing", accent: "bg-info" },
  { stage: "offer", label: "Offer submitted", accent: "bg-warning" },
  { stage: "under-contract", label: "Under contract", accent: "bg-chart-4" },
  { stage: "closing", label: "Closing", accent: "bg-primary" },
  { stage: "closed-won", label: "Closed won", accent: "bg-success" },
];

export default function DealsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Deals"
        description="Drag-and-drop pipeline across six stages. Values are contract price."
        actions={
          <>
            <Select defaultValue="Everyone">
              <SelectTrigger size="sm" className="w-[150px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Everyone">Everyone</SelectItem>
                <SelectItem value="Assigned to me">Assigned to me</SelectItem>
                <SelectItem value="My team">My team</SelectItem>
              </SelectContent>
            </Select>
            <Button variant="outline">
              <Table2 className="size-4" />
              Table view
            </Button>
            <Button>
              <Plus className="size-4" />
              New deal
            </Button>
          </>
        }
      />

      <StatGrid stats={stats} />

      {/* Horizontal scroll keeps all six columns reachable on a laptop. */}
      <div className="scrollbar-slim -mx-4 overflow-x-auto px-4 pb-4 md:-mx-6 md:px-6">
        <div className="flex min-w-max gap-4">
          {columns.map((column) => {
            const columnDeals = deals.filter((d) => d.stage === column.stage);
            const total = columnDeals.reduce((sum, d) => sum + d.value, 0);

            return (
              <section
                key={column.stage}
                className="flex w-[300px] shrink-0 flex-col rounded-xl bg-muted/40"
                aria-label={column.label}
              >
                <header className="flex items-center gap-2 px-3 py-3">
                  <span className={`size-2 rounded-full ${column.accent}`} />
                  <h2 className="text-sm font-medium">{column.label}</h2>
                  <span className="tabular rounded-full bg-background px-1.5 text-xs text-muted-foreground">
                    {columnDeals.length}
                  </span>
                  <span className="tabular ml-auto text-xs font-medium text-muted-foreground">
                    {total > 0 ? formatPrice(total) : "—"}
                  </span>
                </header>

                <div className="flex flex-1 flex-col gap-2.5 px-2.5 pb-2.5">
                  {columnDeals.map((deal) => (
                    <Card
                      key={deal.id}
                      className="group cursor-grab gap-0 p-3.5 transition-shadow hover:shadow-md active:cursor-grabbing"
                    >
                      <div className="flex items-start gap-2">
                        <p className="min-w-0 flex-1 text-sm leading-snug font-medium">
                          {deal.title}
                        </p>
                        <GripVertical className="size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                      </div>

                      <p className="mt-1.5 truncate text-xs text-muted-foreground">
                        {deal.client}
                      </p>

                      <div className="mt-3 flex items-baseline justify-between">
                        <span className="tabular text-base font-semibold">
                          {formatPrice(deal.value)}
                        </span>
                        <span className="tabular text-xs text-muted-foreground">
                          {formatPrice(deal.commission)} comm.
                        </span>
                      </div>

                      <div className="mt-3 space-y-1.5">
                        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                          <span>Probability</span>
                          <span className="tabular font-medium text-foreground">
                            {deal.probability}%
                          </span>
                        </div>
                        <Progress value={deal.probability} className="h-1" />
                      </div>

                      <div className="mt-3.5 flex items-center justify-between gap-2 border-t pt-3">
                        <div className="flex items-center gap-1.5">
                          <UserAvatar user={deal.owner} size="xs" />
                          <span className="text-[11px] text-muted-foreground">
                            {deal.closeDate}
                          </span>
                        </div>
                        <StatusBadge status={deal.priority} dot={false} />
                      </div>
                    </Card>
                  ))}

                  <button className="flex items-center justify-center gap-1.5 rounded-lg border border-dashed py-2.5 text-xs text-muted-foreground transition-colors hover:border-solid hover:bg-background hover:text-foreground">
                    <Plus className="size-3.5" />
                    Add deal
                  </button>
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}
