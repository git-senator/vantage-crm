import type { Metadata } from "next";
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  HardDrive,
  Mail,
  ShieldAlert,
  XCircle,
} from "lucide-react";

import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getAdminOverview } from "@/lib/api/admin";
import { formatNumber } from "@/lib/format";
import { getLocale, getTranslations } from "@/i18n/server";
import { LOCALE_META } from "@/i18n/config";

export const metadata: Metadata = { title: "System" };

/** Bytes, at the scale an operator reads them. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}

export default async function SystemPage() {
  const [overview, t, locale] = await Promise.all([
    getAdminOverview(),
    getTranslations(),
    getLocale(),
  ]);
  const bcp = LOCALE_META[locale].htmlLang;
  const { health, queue, jobs, storage, email, notifications, audit } = overview;

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.sysTitle")}
        description={t("body.sysDesc")}
      />

      {/* ------------------------------------------------------- health */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {t("body.sysHealth")}
            <Badge
              variant={health.status === "ok" ? "outline" : "destructive"}
              className="capitalize"
            >
              {health.status}
            </Badge>
          </CardTitle>
          <CardDescription>{t("body.sysHealthDesc")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {Object.entries(health.components).map(([component, ok]) => (
              <div
                key={component}
                className="flex items-center gap-2 rounded-lg border px-3 py-2.5"
              >
                {ok ? (
                  <CheckCircle2 className="size-4 shrink-0 text-success" />
                ) : (
                  <XCircle className="size-4 shrink-0 text-destructive" />
                )}
                <span className="min-w-0 truncate text-sm capitalize">
                  {component.replace(/_/g, " ")}
                </span>
              </div>
            ))}
          </div>

          {!health.snapshot.fresh ? (
            <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2.5">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" />
              <div className="min-w-0 text-sm">
                <p className="font-medium">{t("body.sysSnapshotBehind")}</p>
                <p className="text-muted-foreground">
                  {health.snapshot.reason ?? t("body.sysSnapshotDefault")}{" "}
                  {t("body.sysSnapshotTail", {
                    date: health.snapshot.last_snapshot_date ?? t("body.sysNever"),
                  })}
                </p>
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* -------------------------------------------------- queue */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="size-4" />
              {t("body.sysQueue")}
            </CardTitle>
            <CardDescription>{t("body.sysQueueDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-3">
              <Figure
                label={t("body.figQueued")}
                value={queue.queued_jobs === null ? "—" : formatNumber(queue.queued_jobs)}
              />
              <Figure
                label={t("body.figWorkers")}
                value={
                  queue.workers_seen === null ? "—" : formatNumber(queue.workers_seen)
                }
                warn={queue.workers_seen === 0}
              />
              <Figure
                label={t("body.figOpenFailures")}
                value={formatNumber(queue.open_failures)}
                warn={queue.open_failures > 0}
              />
            </div>
            {queue.workers_seen === 0 && (queue.queued_jobs ?? 0) > 0 ? (
              <p className="text-xs text-destructive">{t("body.sysNoWorker")}</p>
            ) : null}
          </CardContent>
        </Card>

        {/* ------------------------------------------------ storage */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <HardDrive className="size-4" />
              {t("body.sysStorage")}
            </CardTitle>
            <CardDescription>{t("body.sysStorageDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-3 gap-3">
            <Figure label={t("body.figFiles")} value={formatNumber(storage.attachments)} />
            <Figure label={t("body.figSize")} value={formatBytes(storage.attachment_bytes)} />
            <Figure
              label={t("body.figExports")}
              value={`${formatNumber(storage.export_files)} · ${formatBytes(storage.export_bytes)}`}
            />
            <Figure
              label={t("body.figStuck")}
              value={formatNumber(storage.pending_upload)}
              warn={storage.pending_upload > 0}
            />
            <Figure
              label={t("body.figQuarantined")}
              value={formatNumber(storage.quarantined)}
              warn={storage.quarantined > 0}
            />
            <Figure
              label={t("body.figUnscanned")}
              value={formatNumber(storage.unscanned)}
              warn={storage.unscanned > 0}
            />
          </CardContent>
        </Card>

        {/* -------------------------------------------------- email */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Mail className="size-4" />
              {t("body.sysEmail")}
            </CardTitle>
            <CardDescription>
              {t("body.sysEmailDesc", { hours: email.window_hours })}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-4 gap-3">
              <Figure label={t("body.figSent")} value={formatNumber(email.sent)} />
              <Figure
                label={t("body.figFailed")}
                value={formatNumber(email.failed)}
                warn={email.failed > 0}
              />
              <Figure label={t("body.figQueued")} value={formatNumber(email.queued)} />
              <Figure
                label={t("body.figFailureRate")}
                // Null over no traffic — 0% would read as "all good" on a
                // workspace whose email integration is switched off.
                value={
                  email.failure_rate === null
                    ? "—"
                    : `${email.failure_rate.toFixed(1)}%`
                }
                warn={(email.failure_rate ?? 0) > 5}
              />
            </div>
            {email.recent_failures.length ? (
              <div className="space-y-0 border-t pt-2">
                {email.recent_failures.map((row, index) => (
                  <div
                    key={row.reason}
                    className={`flex items-center justify-between gap-3 py-2 ${index > 0 ? "border-t" : ""}`}
                  >
                    <span className="min-w-0 truncate text-sm">{row.reason}</span>
                    <span className="tabular shrink-0 text-sm">{row.count}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </CardContent>
        </Card>

        {/* ------------------------------------------ notifications */}
        <Card>
          <CardHeader>
            <CardTitle>{t("body.sysNotifications")}</CardTitle>
            <CardDescription>
              {t("body.sysNotificationsDesc", { hours: notifications.window_hours })}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-3">
              <Figure label={t("body.figRaised")} value={formatNumber(notifications.total)} />
              <Figure label={t("body.figUnread")} value={formatNumber(notifications.unread)} />
              <Figure label={t("body.figEmailed")} value={formatNumber(notifications.emailed)} />
            </div>
            {notifications.by_category.length ? (
              <div className="flex flex-wrap gap-2 border-t pt-3">
                {notifications.by_category.map((row) => (
                  <Badge key={row.category} variant="outline">
                    {row.category} · {row.count}
                  </Badge>
                ))}
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>

      {/* --------------------------------------------------- job failures */}
      <Card className="gap-0 overflow-hidden py-0">
        <CardHeader className="border-b py-4">
          <CardTitle>{t("body.sysJobFailures")}</CardTitle>
          <CardDescription>{t("body.sysJobFailuresDesc")}</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {jobs.length ? (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="pl-4">{t("body.colJob")}</TableHead>
                    <TableHead className="text-right">{t("body.colFailures")}</TableHead>
                    <TableHead className="text-right">{t("body.colAttempts")}</TableHead>
                    <TableHead className="text-right">{t("body.colUnresolved")}</TableHead>
                    <TableHead className="pr-4 text-right">{t("body.colLastSeen")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {jobs.map((row) => (
                    <TableRow key={row.job_name}>
                      <TableCell className="pl-4 font-medium">
                        {row.job_name}
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {row.failures}
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {row.attempts}
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {row.unresolved}
                      </TableCell>
                      <TableCell className="tabular pr-4 text-right text-muted-foreground">
                        {new Date(row.last_failed_at).toLocaleString(bcp)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <div className="p-6">
              <EmptyState
                icon={CheckCircle2}
                compact
                title={t("body.sysNoFailures")}
                description={t("body.sysNoFailuresDesc")}
              />
            </div>
          )}
        </CardContent>
      </Card>

      {/* ------------------------------------------------------- audit */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldAlert className="size-4" />
            {t("body.sysAudit")}
          </CardTitle>
          <CardDescription>
            {t("body.sysAuditDesc", { hours: audit.window_hours })}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <Figure label={t("body.figEvents")} value={formatNumber(audit.total_events)} />
            <Figure
              label={t("body.figDenied")}
              value={formatNumber(audit.denied)}
              warn={audit.denied > 10}
            />
            <Figure label={t("body.figExports")} value={formatNumber(audit.exports)} />
          </div>

          <div className="grid gap-6 border-t pt-4 md:grid-cols-2">
            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">
                {t("body.sysTopActions")}
              </p>
              <div className="space-y-0">
                {audit.by_action.slice(0, 8).map((row, index) => (
                  <div
                    key={row.action}
                    className={`flex items-center justify-between gap-3 py-1.5 ${index > 0 ? "border-t" : ""}`}
                  >
                    <span className="min-w-0 truncate text-sm">{row.action}</span>
                    <span className="tabular shrink-0 text-sm">{row.count}</span>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">
                {t("body.sysTopAccounts")}
              </p>
              <div className="space-y-0">
                {audit.by_actor.slice(0, 8).map((row, index) => (
                  <div
                    key={`${row.actor_id ?? "system"}-${row.actor_email}`}
                    className={`flex items-center justify-between gap-3 py-1.5 ${index > 0 ? "border-t" : ""}`}
                  >
                    <span className="min-w-0 truncate text-sm">
                      {row.actor_email}
                    </span>
                    <span className="tabular shrink-0 text-sm">{row.count}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function Figure({
  label,
  value,
  warn = false,
}: {
  label: string;
  value: string;
  warn?: boolean;
}) {
  return (
    <div className="rounded-lg border px-3 py-2.5">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={`tabular truncate text-lg font-semibold ${warn ? "text-destructive" : ""}`}
      >
        {value}
      </p>
    </div>
  );
}
