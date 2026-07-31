import type { Metadata } from "next";
import Link from "next/link";
import { Activity, FileSpreadsheet, Home, Share2, Target, Users } from "lucide-react";

import {
  ConversionFunnel,
  DealMixChart,
  LeadSourceChart,
} from "@/components/reports/charts";
import { ForecastPanel } from "@/components/reports/forecast-panel";
import { MetricGrid } from "@/components/reports/metric-grid";
import { PeriodPicker } from "@/components/reports/period-picker";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getDashboard } from "@/lib/api/analytics";
import type { PeriodName } from "@/lib/api/types";
import { getTranslations } from "@/i18n/server";
import { formatMoney } from "@/lib/metrics";

export const metadata: Metadata = { title: "Reports & Analytics" };

const PERIODS: PeriodName[] = ["today", "week", "month", "quarter", "year"];

function resolvePeriod(value: string | undefined): PeriodName {
  return PERIODS.includes(value as PeriodName) ? (value as PeriodName) : "month";
}

export default async function ReportsPage({
  searchParams,
}: {
  searchParams: Promise<{ period?: string }>;
}) {
  const period = resolvePeriod((await searchParams).period);
  const t = await getTranslations();
  // One call for the whole screen: the panels share a period and a scope
  // resolution, and separate calls would each re-resolve the caller's team
  // membership and could disagree about the moment they describe.
  const dashboard = await getDashboard("executive", { period });

  const topRevenue = dashboard.agents.reduce(
    (max, row) => Math.max(max, Number(row.revenue) || 0),
    0,
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.reportsTitle")}
        description={t("body.reportsDesc", { label: t(`body.period_${period}`) })}
        actions={
          <>
            <PeriodPicker value={period} />
            <Button variant="outline" render={<Link href="/reports/builder" />}>
              <FileSpreadsheet className="size-4" />
              {t("body.repBuildReport")}
            </Button>
            <Button variant="outline">
              <Share2 className="size-4" />
              {t("body.repShare")}
            </Button>
          </>
        }
      />

      <MetricGrid metrics={dashboard.metrics} t={t} />

      {dashboard.forecast ? (
        <ForecastPanel forecast={dashboard.forecast} t={t} />
      ) : null}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>{t("body.repLeadsBySource")}</CardTitle>
            <CardDescription>{t("body.repLeadsBySourceDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            {dashboard.sources.length ? (
              <LeadSourceChart
                data={dashboard.sources.map((row) => ({
                  source: row.source,
                  count: row.created,
                }))}
                className="h-[260px] w-full"
              />
            ) : (
              <EmptyState
                icon={Users}
                compact
                title={t("body.repNoLeads")}
                description={t("body.repNoLeadsDesc")}
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("body.repPipelineByStage")}</CardTitle>
            <CardDescription>{t("body.repPipelineByStageDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            {dashboard.stages.length ? (
              <ConversionFunnel
                data={dashboard.stages.map((stage) => ({
                  stage: stage.stage_name,
                  value: stage.deal_count,
                }))}
              />
            ) : (
              <EmptyState
                icon={Target}
                compact
                title={t("body.repNoOpenDeals")}
                description={t("body.repNoOpenDealsDesc")}
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("body.repWhyLost")}</CardTitle>
            <CardDescription>{t("body.repWhyLostDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            {dashboard.reasons.length ? (
              <DealMixChart
                data={dashboard.reasons.map((row) => ({
                  name: row.reason,
                  value: row.count,
                }))}
                className="h-[260px] w-full"
              />
            ) : (
              <EmptyState
                icon={Home}
                compact
                title={t("body.repNothingLost")}
                description={t("body.repNothingLostDesc")}
              />
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        {/* --------------------------------------------- leaderboard */}
        <Card className="gap-0 overflow-hidden py-0">
          <CardHeader className="border-b py-4">
            <CardTitle>{t("body.repLeaderboard")}</CardTitle>
            <CardDescription>{t("body.repLeaderboardDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {dashboard.agents.length ? (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="w-10 pl-4">#</TableHead>
                      <TableHead>{t("body.repColAgent")}</TableHead>
                      <TableHead className="min-w-[180px]">{t("body.repColRevenue")}</TableHead>
                      <TableHead className="text-right">{t("body.repColDealsWon")}</TableHead>
                      <TableHead className="pr-4 text-right">{t("body.repColConverted")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {dashboard.agents.map((row, index) => (
                      <TableRow key={row.user_id}>
                        <TableCell className="tabular pl-4 text-muted-foreground">
                          {index + 1}
                        </TableCell>
                        <TableCell>
                          <p className="truncate text-sm font-medium">
                            {row.full_name}
                          </p>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-3">
                            <Progress
                              value={
                                topRevenue
                                  ? ((Number(row.revenue) || 0) / topRevenue) *
                                    100
                                  : 0
                              }
                              className="h-1.5 w-24"
                            />
                            <span className="tabular text-sm font-medium">
                              {formatMoney(row.revenue)}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="tabular text-right">
                          {row.deals_won}
                        </TableCell>
                        <TableCell className="tabular pr-4 text-right">
                          {row.leads_converted}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <div className="p-6">
                <EmptyState
                  icon={Target}
                  compact
                  title={t("body.repNoProduction")}
                  description={t("body.repNoProductionDesc")}
                />
              </div>
            )}
          </CardContent>
        </Card>

        {/* ----------------------------------------------- velocity */}
        <Card>
          <CardHeader>
            <CardTitle>{t("body.repVelocity")}</CardTitle>
            <CardDescription>{t("body.repVelocityDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-0">
            {dashboard.velocity.length ? (
              dashboard.velocity.map((row, index) => (
                <div
                  key={row.stage_id}
                  className={`flex items-center justify-between gap-3 py-3 ${index > 0 ? "border-t" : ""}`}
                >
                  <span className="min-w-0 truncate text-sm">
                    {row.stage_name}
                  </span>
                  <div className="flex shrink-0 items-baseline gap-3">
                    <span className="tabular text-xs text-muted-foreground">
                      {t("body.repMoved", { n: row.transitions })}
                    </span>
                    <span className="tabular text-sm font-semibold">
                      {Number(row.mean_days).toFixed(1)}d
                    </span>
                  </div>
                </div>
              ))
            ) : (
              <EmptyState
                icon={Activity}
                compact
                title={t("body.repNoTransitions")}
                description={t("body.repNoTransitionsDesc")}
              />
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
