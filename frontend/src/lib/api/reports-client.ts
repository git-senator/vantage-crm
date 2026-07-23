"use client";

import { apiRequest } from "@/lib/api/client";
import type {
  ExportFormat,
  ReportDefinition,
  ReportPreview,
  ReportRun,
  ReportSchedule,
  ReportSpec,
} from "@/lib/api/types";

/**
 * Report mutations, from the builder.
 *
 * `preview` runs on every meaningful change; `requestExport` produces a file
 * and is audited. That split is deliberate — an audit log dominated by previews
 * is one nobody reads.
 */

export async function previewReport(spec: ReportSpec): Promise<ReportPreview> {
  return apiRequest<ReportPreview>("/reports/preview", {
    method: "POST",
    body: spec,
  });
}

export async function createReport(input: {
  name: string;
  description?: string | null;
  definition: ReportSpec;
  is_shared?: boolean;
  schedule?: ReportSchedule;
  schedule_format?: ExportFormat;
  recipients?: string[];
}): Promise<ReportDefinition> {
  return apiRequest<ReportDefinition>("/reports", {
    method: "POST",
    body: input,
  });
}

export async function updateReport(
  id: string,
  changes: Partial<{
    name: string;
    description: string | null;
    definition: ReportSpec;
    is_shared: boolean;
    schedule: ReportSchedule;
    schedule_format: ExportFormat;
    recipients: string[];
  }>,
): Promise<ReportDefinition> {
  return apiRequest<ReportDefinition>(`/reports/${id}`, {
    method: "PATCH",
    body: changes,
  });
}

export async function deleteReport(id: string): Promise<void> {
  await apiRequest<void>(`/reports/${id}`, { method: "DELETE" });
}

/**
 * Queue an export. Returns a `queued` run, not a file.
 *
 * The work happens in a worker: rendering 50,000 rows into a spreadsheet takes
 * seconds and tens of megabytes, and holding a request open for it would tie up
 * a connection while the browser waits. Poll `getRun` for the outcome.
 */
export async function requestExport(input: {
  definition_id?: string;
  definition?: ReportSpec;
  format: ExportFormat;
}): Promise<ReportRun> {
  return apiRequest<ReportRun>("/reports/export", {
    method: "POST",
    body: input,
  });
}

export async function getRun(runId: string): Promise<ReportRun> {
  return apiRequest<ReportRun>(`/reports/runs/${runId}`);
}

/**
 * A fresh signed download link.
 *
 * Minted on demand rather than stored: the URL expires in five minutes, so one
 * captured at export time would be dead by the time anyone clicked it.
 */
export async function getDownloadUrl(runId: string): Promise<string> {
  const response = await apiRequest<{ url: string; expires_in: number }>(
    `/reports/runs/${runId}/download`,
  );
  return response.url;
}
