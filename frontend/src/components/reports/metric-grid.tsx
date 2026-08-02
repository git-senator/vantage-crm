import { StatGrid, type Stat } from "@/components/shared/stat-card";
import type { TranslateFn } from "@/i18n/translate";
import type { Metric } from "@/lib/api/types";
import { metricLabel } from "@/lib/i18n-labels";
import {
  formatMetric,
  invertDelta,
  metricDelta,
  metricIcon,
} from "@/lib/metrics";

/**
 * KPI tiles, driven entirely by what the API sent.
 *
 * Nothing here decides what a metric means. The label, the unit and which
 * direction is good all come from the registry — hard-coding any of them is how
 * a dashboard ends up painting rising overdue tasks green, and how a label
 * drifts out of step with the number under it.
 *
 * A metric with a `null` value renders as "—". That is the caller having no
 * grant on the underlying entity, not a zero, and the two must not look alike.
 */
export function MetricGrid({
  metrics,
  t,
}: {
  metrics: Metric[];
  t: TranslateFn;
}) {
  if (!metrics.length) return null;

  const stats: Stat[] = metrics.map((metric) => ({
    // Label is server-provided (the metric registry) but translated by its
    // stable key, falling back to the server text; see note in reports page.
    label: metricLabel(t, metric.key, metric.label),
    value: formatMetric(metric),
    delta: metricDelta(metric),
    hint:
      metric.value === null
        ? t("body.repNotVisible")
        : t("body.repVsPrevious"),
    invertDelta: invertDelta(metric),
    icon: metricIcon(metric.key),
  }));

  return <StatGrid stats={stats} />;
}
