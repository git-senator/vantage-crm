import "server-only";

import { apiFetch } from "@/lib/api/server";
import type {
  AnalyticsDashboard,
  DashboardListing,
  ForecastResponse,
  Goal,
  KpiResponse,
  MetricDefinition,
  PeriodName,
  SeriesResponse,
} from "@/lib/api/types";

/**
 * Analytics reads. Every one of them takes the same period parameters, so a
 * screen can be moved to any window without learning a second convention.
 *
 * All numeric values arrive as strings and stay strings until they are
 * formatted. Parsing them into JS numbers on the way through is how a revenue
 * figure loses the precision the NUMERIC column exists to protect.
 */

export interface PeriodQuery {
  period?: PeriodName;
  start?: string;
  end?: string;
}

function periodParams(query: PeriodQuery = {}): string {
  const params = new URLSearchParams();
  if (query.period) params.set("period", query.period);
  if (query.start) params.set("start", query.start);
  if (query.end) params.set("end", query.end);
  const rendered = params.toString();
  return rendered ? `?${rendered}` : "";
}

/** The metric catalogue. Served from the registry so the UI never hard-codes a
 * label, a unit, or which direction is good. */
export async function getMetricCatalogue(): Promise<MetricDefinition[]> {
  return apiFetch<MetricDefinition[]>("/analytics/metrics");
}

export async function getKpis(query: PeriodQuery = {}): Promise<KpiResponse> {
  return apiFetch<KpiResponse>(`/analytics/kpis${periodParams(query)}`);
}

/** Daily points for one metric — snapshots for history, today computed live. */
export async function getSeries(
  metricKey: string,
  days = 30,
): Promise<SeriesResponse> {
  return apiFetch<SeriesResponse>(`/analytics/series/${metricKey}?days=${days}`);
}

export async function listDashboards(): Promise<DashboardListing[]> {
  return apiFetch<DashboardListing[]>("/analytics/dashboards");
}

/**
 * One named dashboard, assembled server-side.
 *
 * A single call rather than one per panel: the panels share a period and a
 * scope resolution, and separate calls would each re-resolve the caller's team
 * membership and could render panels captured at different moments.
 */
export async function getDashboard(
  key: string,
  query: PeriodQuery = {},
): Promise<AnalyticsDashboard> {
  return apiFetch<AnalyticsDashboard>(
    `/analytics/dashboards/${key}${periodParams(query)}`,
  );
}

export async function getForecast(
  query: PeriodQuery = {},
): Promise<ForecastResponse> {
  return apiFetch<ForecastResponse>(`/analytics/forecast${periodParams(query)}`);
}

export async function listGoals(): Promise<Goal[]> {
  return apiFetch<Goal[]>("/analytics/goals");
}
