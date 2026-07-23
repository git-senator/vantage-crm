import {
  BarChart3,
  CircleDollarSign,
  Clock,
  FileText,
  Home,
  Percent,
  Target,
  TrendingUp,
  Users,
  type LucideIcon,
} from "lucide-react";

import { formatNumber, formatPrice } from "@/lib/format";
import type { Metric } from "@/lib/api/types";

/**
 * Rendering metrics.
 *
 * Values arrive as strings and are parsed **once, here, at the display
 * boundary**. Everywhere else they stay strings: a revenue figure that has been
 * through JS arithmetic on the way to a component has already lost the
 * precision the NUMERIC column exists to protect, and the loss is invisible
 * until somebody reconciles a commission.
 */

/** How a metric's unit maps to a formatter. Driven by the API's own `unit`
 * field so the frontend never keeps a second opinion about what a metric is. */
export function formatMetric(metric: Metric): string {
  if (metric.value === null) return "—";

  const numeric = Number(metric.value);
  if (!Number.isFinite(numeric)) return metric.value;

  switch (metric.unit) {
    case "currency":
      return formatPrice(numeric);
    case "percent":
      return `${numeric.toFixed(1)}%`;
    case "days":
      return `${Math.round(numeric)} ${Math.round(numeric) === 1 ? "day" : "days"}`;
    default:
      return formatNumber(Math.round(numeric));
  }
}

/** A money string from the API, formatted. Never `Number()` at a call site. */
export function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? formatPrice(numeric) : "—";
}

export function formatCount(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? formatNumber(Math.round(numeric)) : "—";
}

/**
 * A metric's delta, as the StatCard wants it.
 *
 * `undefined` when the API sent null — which it does when the previous period
 * was zero. Rendering "+100%" against nothing invites a comparison that does
 * not exist, so the tile shows no delta at all.
 */
export function metricDelta(metric: Metric): number | undefined {
  if (metric.delta_percent === null) return undefined;
  const numeric = Number(metric.delta_percent);
  return Number.isFinite(numeric) ? numeric : undefined;
}

/**
 * Whether a *rising* value is bad news.
 *
 * Read from the metric, never inferred: the API says which direction is good,
 * and guessing here is how a dashboard ends up painting rising overdue tasks
 * green.
 */
export function invertDelta(metric: Metric): boolean {
  return !metric.higher_is_better;
}

const ICONS: Record<string, LucideIcon> = {
  revenue_won: CircleDollarSign,
  average_deal_value: CircleDollarSign,
  pipeline_open_value: TrendingUp,
  pipeline_weighted_value: TrendingUp,
  deals_won: Target,
  deals_lost: Target,
  deals_created: BarChart3,
  win_rate: Percent,
  lead_conversion_rate: Percent,
  leads_created: Users,
  leads_converted: Users,
  leads_open: Users,
  clients_created: Users,
  listings_active: Home,
  listings_sold: Home,
  average_days_on_market: Clock,
  sales_cycle_days: Clock,
  tasks_completed: FileText,
  tasks_overdue: Clock,
  activities_logged: FileText,
};

export function metricIcon(key: string): LucideIcon {
  return ICONS[key] ?? BarChart3;
}

/** A metric list keyed for lookup, so a panel can ask for one by name. */
export function byKey(metrics: Metric[]): Map<string, Metric> {
  return new Map(metrics.map((metric) => [metric.key, metric]));
}
