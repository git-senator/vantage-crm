import "server-only";

import { apiFetch } from "@/lib/api/server";
import type {
  ReportDataset,
  ReportDefinition,
  ReportRun,
} from "@/lib/api/types";

/**
 * Report reads.
 *
 * The dataset catalogue arrives already filtered to what the caller may query,
 * so the builder never offers a choice that would be refused — and never has to
 * hold its own copy of the permission matrix to work that out.
 */

export async function listDatasets(): Promise<ReportDataset[]> {
  return apiFetch<ReportDataset[]>("/reports/datasets");
}

export async function listReports(): Promise<ReportDefinition[]> {
  return apiFetch<ReportDefinition[]>("/reports");
}

export async function getReport(id: string): Promise<ReportDefinition> {
  return apiFetch<ReportDefinition>(`/reports/${id}`);
}

/**
 * Recent runs. `definition_id` narrows to one saved report.
 *
 * Runs the caller did not request are visible only with `reports.export` — the
 * history names what people exported, which is closer to an audit record than
 * to a report.
 */
export async function listRuns(definitionId?: string): Promise<ReportRun[]> {
  const query = definitionId ? `?definition_id=${definitionId}` : "";
  return apiFetch<ReportRun[]>(`/reports/runs/history${query}`);
}
