import type { Metadata } from "next";
import {
  CircleDollarSign,
  Clock,
  Download,
  Percent,
  Share2,
  Target,
} from "lucide-react";

import { RevenueChart } from "@/components/dashboard/revenue-chart";
import {
  ConversionFunnel,
  DealMixChart,
  LeadSourceChart,
} from "@/components/reports/charts";
import { PageHeader } from "@/components/shared/page-header";
import { StatGrid, type Stat } from "@/components/shared/stat-card";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatPrice } from "@/lib/format";
import {
  agentLeaderboard,
  conversionFunnel,
  dealMix,
  leadsBySource,
  revenueByMonth,
} from "@/lib/mock-data";

export const metadata: Metadata = { title: "Reports & Analytics" };

const stats: Stat[] = [
  { label: "Closed volume YTD", value: "$26.1M", delta: 18.9, hint: "vs. 2025", icon: CircleDollarSign },
  { label: "Gross commission", value: "$652K", delta: 15.2, hint: "YTD", icon: Percent },
  { label: "Lead conversion", value: "5.3%", delta: 1.1, hint: "508 → 27 closed", icon: Target },
  { label: "Avg. sales cycle", value: "38 days", delta: -6.2, hint: "faster than Q1", invertDelta: true, icon: Clock },
];

const marketBenchmarks = [
  { label: "Median sale price", ours: "$1.42M", market: "$1.31M", better: true },
  { label: "Sale-to-list ratio", ours: "102.4%", market: "99.8%", better: true },
  { label: "Days on market", ours: "27", market: "31", better: true },
  { label: "Listings sold above ask", ours: "61%", market: "54%", better: true },
  { label: "Price reductions", ours: "12%", market: "9%", better: false },
];

export default function ReportsPage() {
  const topVolume = agentLeaderboard[0].volume;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Reports & Analytics"
        description="Performance across volume, conversion and team production."
        actions={
          <>
            <Select defaultValue="Year to date">
              <SelectTrigger size="sm" className="w-[150px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Last 30 days">Last 30 days</SelectItem>
                <SelectItem value="This quarter">This quarter</SelectItem>
                <SelectItem value="Year to date">Year to date</SelectItem>
                <SelectItem value="Trailing 12 months">Trailing 12 months</SelectItem>
              </SelectContent>
            </Select>
            <Button variant="outline">
              <Share2 className="size-4" />
              Share
            </Button>
            <Button variant="outline">
              <Download className="size-4" />
              Export
            </Button>
          </>
        }
      />

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="production">Production</TabsTrigger>
          <TabsTrigger value="marketing">Marketing</TabsTrigger>
          <TabsTrigger value="forecast">Forecast</TabsTrigger>
        </TabsList>
      </Tabs>

      <StatGrid stats={stats} />

      <Card>
        <CardHeader>
          <CardTitle>Revenue performance</CardTitle>
          <CardDescription>
            Closed volume against open pipeline, in millions.
          </CardDescription>
          <CardAction>
            <Button variant="outline" size="sm">
              Compare to 2025
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent>
          <RevenueChart data={revenueByMonth} className="h-[300px] w-full" />
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Leads by source</CardTitle>
            <CardDescription>Last 90 days.</CardDescription>
          </CardHeader>
          <CardContent>
            <LeadSourceChart data={leadsBySource} className="h-[260px] w-full" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Conversion funnel</CardTitle>
            <CardDescription>Stage-over-stage drop-off.</CardDescription>
          </CardHeader>
          <CardContent>
            <ConversionFunnel data={conversionFunnel} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Deal mix</CardTitle>
            <CardDescription>Share of closed volume by type.</CardDescription>
          </CardHeader>
          <CardContent>
            <DealMixChart data={dealMix} className="h-[260px] w-full" />
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        {/* --------------------------------------------- leaderboard */}
        <Card className="gap-0 overflow-hidden py-0">
          <CardHeader className="border-b py-4">
            <CardTitle>Agent leaderboard</CardTitle>
            <CardDescription>Ranked by closed volume, year to date.</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="w-10 pl-4">#</TableHead>
                    <TableHead>Agent</TableHead>
                    <TableHead className="min-w-[180px]">Volume</TableHead>
                    <TableHead className="text-right">Deals</TableHead>
                    <TableHead className="pr-4 text-right">Win rate</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {agentLeaderboard.map((row, index) => (
                    <TableRow key={row.agent.id}>
                      <TableCell className="tabular pl-4 text-muted-foreground">
                        {index + 1}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2.5">
                          <UserAvatar user={row.agent} size="sm" />
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">
                              {row.agent.name}
                            </p>
                            <p className="truncate text-xs text-muted-foreground">
                              {row.agent.role}
                            </p>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-3">
                          <Progress
                            value={(row.volume / topVolume) * 100}
                            className="h-1.5 w-24"
                          />
                          <span className="tabular text-sm font-medium">
                            {formatPrice(row.volume)}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {row.deals}
                      </TableCell>
                      <TableCell className="tabular pr-4 text-right">
                        {row.winRate}%
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        {/* ---------------------------------------------- benchmarks */}
        <Card>
          <CardHeader>
            <CardTitle>Market benchmarks</CardTitle>
            <CardDescription>
              Your brokerage against the SF Bay Area median.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-0">
            {marketBenchmarks.map((row, index) => (
              <div
                key={row.label}
                className={`flex items-center justify-between gap-3 py-3 ${index > 0 ? "border-t" : ""}`}
              >
                <span className="min-w-0 truncate text-sm">{row.label}</span>
                <div className="flex shrink-0 items-baseline gap-3">
                  <span className="tabular text-xs text-muted-foreground">
                    mkt {row.market}
                  </span>
                  <span
                    className={`tabular text-sm font-semibold ${row.better ? "text-success" : "text-destructive"}`}
                  >
                    {row.ours}
                  </span>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
