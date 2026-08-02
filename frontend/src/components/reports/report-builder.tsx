"use client";

import { useMemo, useState, useTransition } from "react";
import { Download, Loader2, Play, Save } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/shared/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { useTranslation } from "@/i18n/language-provider";
import {
  createReport,
  getDownloadUrl,
  getRun,
  previewReport,
  requestExport,
} from "@/lib/api/reports-client";
import type {
  ExportFormat,
  ReportDataset,
  ReportDefinition,
  ReportPreview,
  ReportRun,
  ReportSpec,
} from "@/lib/api/types";

/**
 * The report builder.
 *
 * Composes a *specification* — never a query. Every field name it sends is one
 * the server handed it in the dataset catalogue, and the server re-validates
 * each one anyway; this component could not construct SQL if it tried, which is
 * the point of the design rather than a property of this file.
 *
 * Preview is synchronous and capped. Export is queued and polled, because
 * rendering fifty thousand rows into a spreadsheet is not something to do while
 * a browser holds a connection open.
 */

const FORMATS: { value: ExportFormat; label: string }[] = [
  { value: "xlsx", label: "Excel (.xlsx)" },
  { value: "csv", label: "CSV" },
  { value: "pdf", label: "PDF" },
];

/** How long to keep asking about a queued export before giving up on the poll.
 * The run itself carries on; only the polling stops. */
const POLL_ATTEMPTS = 30;
const POLL_INTERVAL_MS = 2000;

export function ReportBuilder({
  datasets,
  saved,
  runs,
}: {
  datasets: ReportDataset[];
  saved: ReportDefinition[];
  runs: ReportRun[];
}) {
  const { t } = useTranslation();
  const [datasetKey, setDatasetKey] = useState(datasets[0]?.key ?? "");
  const [columns, setColumns] = useState<string[]>([]);
  const [groupBy, setGroupBy] = useState<string>("");
  const [format, setFormat] = useState<ExportFormat>("xlsx");
  const [name, setName] = useState("");
  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [running, startRun] = useTransition();
  const [exporting, setExporting] = useState(false);
  const [history, setHistory] = useState(runs);

  const dataset = useMemo(
    () => datasets.find((entry) => entry.key === datasetKey),
    [datasets, datasetKey],
  );

  const spec: ReportSpec = useMemo(
    () => ({
      dataset: datasetKey,
      columns: groupBy ? [] : columns,
      filters: [],
      group_by: groupBy ? [groupBy] : [],
      aggregates: [],
      sort_desc: true,
      limit: 100,
      include_sensitive: false,
    }),
    [datasetKey, columns, groupBy],
  );

  function chooseDataset(key: string | null) {
    if (!key) return;
    setDatasetKey(key);
    // Columns belong to a dataset. Carrying them across would send names the
    // new dataset does not have, and the server would reject the whole report
    // rather than the one stale field.
    setColumns([]);
    setGroupBy("");
    setPreview(null);
  }

  function toggleColumn(key: string) {
    setColumns((current) =>
      current.includes(key)
        ? current.filter((entry) => entry !== key)
        : [...current, key],
    );
  }

  function runPreview() {
    startRun(async () => {
      try {
        setPreview(await previewReport(spec));
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : t("body.rbPreviewFailed"),
        );
      }
    });
  }

  async function runExport() {
    setExporting(true);
    try {
      let run = await requestExport({ definition: spec, format });
      setHistory((current) => [run, ...current]);

      for (let attempt = 0; attempt < POLL_ATTEMPTS; attempt += 1) {
        if (run.status !== "queued" && run.status !== "running") break;
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        run = await getRun(run.id);
      }

      setHistory((current) =>
        current.map((entry) => (entry.id === run.id ? run : entry)),
      );

      if (run.status === "failed") {
        toast.error(run.error ?? t("body.rbExportFailed"));
        return;
      }
      if (run.status === "queued" || run.status === "running") {
        toast.info(t("body.rbStillRunning"));
        return;
      }
      if (run.status === "partial") {
        // Said out loud rather than silently handing over a prefix.
        toast.warning(
          t("body.rbExportedPartial", {
            n: run.row_count.toLocaleString(),
            total: run.total_rows.toLocaleString(),
          }),
        );
      }
      window.open(await getDownloadUrl(run.id), "_blank", "noopener");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("body.rbExportFailed"));
    } finally {
      setExporting(false);
    }
  }

  async function save() {
    if (!name.trim()) {
      toast.error(t("body.rbNameFirst"));
      return;
    }
    try {
      await createReport({ name: name.trim(), definition: spec });
      toast.success(t("body.rbSaved"));
      setName("");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("body.rbCouldNotSave"));
    }
  }

  if (!datasets.length) {
    return (
      <EmptyState
        icon={Play}
        title={t("body.rbNothingToReport")}
        description={t("body.rbNoAccess")}
      />
    );
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
      {/* ------------------------------------------------------ controls */}
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>{t("body.rbDataset")}</CardTitle>
            <CardDescription>{t("body.rbOnlyQueryable")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Select value={datasetKey} onValueChange={chooseDataset}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {datasets.map((entry) => (
                  <SelectItem key={entry.key} value={entry.key}>
                    {entry.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {dataset ? (
              <p className="text-xs text-muted-foreground">
                {dataset.description}
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("body.rbGroupBy")}</CardTitle>
            <CardDescription>{t("body.rbGroupByDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <Select
              value={groupBy || "none"}
              onValueChange={(value) => setGroupBy(!value || value === "none" ? "" : value)}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">{t("body.rbNoGrouping")}</SelectItem>
                {(dataset?.fields ?? [])
                  .filter((field) => field.groupable)
                  .map((field) => (
                    <SelectItem key={field.key} value={field.key}>
                      {field.label}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </CardContent>
        </Card>

        {!groupBy ? (
          <Card>
            <CardHeader>
              <CardTitle>{t("body.rbColumns")}</CardTitle>
              <CardDescription>{t("body.rbColumnsDesc")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2.5">
              {(dataset?.fields ?? []).map((field) => (
                <label
                  key={field.key}
                  className="flex items-center gap-2.5 text-sm"
                >
                  <Checkbox
                    checked={columns.includes(field.key)}
                    onCheckedChange={() => toggleColumn(field.key)}
                    disabled={field.sensitive}
                  />
                  <span className="min-w-0 truncate">{field.label}</span>
                  {field.sensitive ? (
                    <Badge variant="outline" className="ml-auto shrink-0">
                      {t("body.rbSensitive")}
                    </Badge>
                  ) : null}
                </label>
              ))}
            </CardContent>
          </Card>
        ) : null}
      </div>

      {/* ------------------------------------------------------- results */}
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>{t("body.rbPreview")}</CardTitle>
            <CardDescription>{t("body.rbPreviewDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={runPreview} disabled={running}>
                {running ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Play className="size-4" />
                )}
                {t("body.rbRunPreview")}
              </Button>

              <Select
                value={format}
                onValueChange={(value) => setFormat(value as ExportFormat)}
              >
                <SelectTrigger size="sm" className="w-[150px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FORMATS.map((entry) => (
                    <SelectItem key={entry.value} value={entry.value}>
                      {entry.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Button
                variant="outline"
                onClick={runExport}
                disabled={exporting}
              >
                {exporting ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Download className="size-4" />
                )}
                {t("buttons.export")}
              </Button>

              <div className="ml-auto flex items-center gap-2">
                <Label htmlFor="report-name" className="sr-only">
                  {t("body.rbReportName")}
                </Label>
                <Input
                  id="report-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder={t("body.rbSaveAs")}
                  className="h-9 w-[180px]"
                />
                <Button variant="outline" onClick={save}>
                  <Save className="size-4" />
                  {t("buttons.save")}
                </Button>
              </div>
            </div>

            {preview ? (
              <>
                {preview.truncated ? (
                  <p className="text-xs text-muted-foreground">
                    {t("body.rbShowingRows", {
                      n: preview.row_count.toLocaleString(),
                      total: preview.total_rows.toLocaleString(),
                    })}
                  </p>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    {t("body.rbRowsDot", {
                      n: preview.row_count.toLocaleString(),
                    })}
                  </p>
                )}
                <div className="overflow-x-auto rounded-lg border">
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        {preview.headers.map((header) => (
                          <TableHead key={header}>{header}</TableHead>
                        ))}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {preview.rows.map((row, index) => (
                        <TableRow key={index}>
                          {row.map((cell, cellIndex) => (
                            <TableCell key={cellIndex} className="max-w-[240px] truncate">
                              {cell === null || cell === undefined
                                ? "—"
                                : String(cell)}
                            </TableCell>
                          ))}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </>
            ) : (
              <EmptyState
                icon={Play}
                compact
                title={t("body.rbNothingRun")}
                description={t("body.rbNothingRunDesc")}
              />
            )}
          </CardContent>
        </Card>

        {saved.length ? (
          <Card>
            <CardHeader>
              <CardTitle>{t("body.rbSavedReports")}</CardTitle>
              <CardDescription>{t("body.rbSavedReportsDesc")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-0">
              {saved.map((report, index) => (
                <div
                  key={report.id}
                  className={`flex items-center justify-between gap-3 py-3 ${index > 0 ? "border-t" : ""}`}
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{report.name}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {report.dataset}
                      {report.schedule !== "none" ? ` · ${report.schedule}` : ""}
                    </p>
                  </div>
                  {report.is_shared ? (
                    <Badge variant="outline" className="shrink-0">
                      {t("body.rbShared")}
                    </Badge>
                  ) : null}
                </div>
              ))}
            </CardContent>
          </Card>
        ) : null}

        {history.length ? (
          <Card>
            <CardHeader>
              <CardTitle>{t("body.rbExportHistory")}</CardTitle>
              <CardDescription>{t("body.rbExportHistoryDesc")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-0">
              {history.slice(0, 10).map((run, index) => (
                <div
                  key={run.id}
                  className={`flex items-center justify-between gap-3 py-3 ${index > 0 ? "border-t" : ""}`}
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{run.name}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {run.format.toUpperCase()} ·{" "}
                      {t("body.rbRows", { n: run.row_count.toLocaleString() })}
                      {run.status === "partial"
                        ? ` / ${run.total_rows.toLocaleString()}`
                        : ""}
                    </p>
                  </div>
                  <RunBadge status={run.status} t={t} />
                </div>
              ))}
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  );
}

/** `partial` gets its own colour: it is a success, but not a complete one. */
function RunBadge({
  status,
  t,
}: {
  status: ReportRun["status"];
  t: ReturnType<typeof useTranslation>["t"];
}) {
  const variant =
    status === "failed"
      ? "destructive"
      : status === "partial"
        ? "secondary"
        : "outline";
  return (
    <Badge variant={variant} className="shrink-0">
      {t(`body.rbStatus_${status}`)}
    </Badge>
  );
}
