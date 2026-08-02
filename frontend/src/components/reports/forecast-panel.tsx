import { ArrowDownRight, ArrowUpRight } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import type { TranslateFn } from "@/i18n/translate";
import type { ForecastResponse } from "@/lib/api/types";
import { stageLabel } from "@/lib/i18n-labels";
import { formatMoney } from "@/lib/metrics";

/**
 * Booked revenue plus weighted pipeline.
 *
 * Presented as three separate numbers rather than one headline, because they
 * carry different amounts of certainty: `booked` has happened, `weighted` is an
 * estimate, and `projected` is their sum. Collapsing them into a single figure
 * would hide which part is a fact and which is a hope, which is exactly the
 * distinction anyone planning against it needs.
 */
export function ForecastPanel({
  forecast,
  t,
}: {
  forecast: ForecastResponse;
  t: TranslateFn;
}) {
  const booked = Number(forecast.booked) || 0;
  const projected = Number(forecast.projected) || 0;
  const previous = forecast.previous_actual
    ? Number(forecast.previous_actual)
    : null;

  const bookedShare = projected > 0 ? (booked / projected) * 100 : 0;
  const change =
    previous && previous > 0 ? ((booked - previous) / previous) * 100 : null;
  const ChangeIcon = (change ?? 0) >= 0 ? ArrowUpRight : ArrowDownRight;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("body.fcForecast")}</CardTitle>
        <CardDescription>{t("body.fcForecastDesc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <p className="text-xs text-muted-foreground">{t("body.fcBooked")}</p>
            <p className="tabular text-2xl font-semibold">
              {formatMoney(forecast.booked)}
            </p>
            <p className="text-xs text-muted-foreground">{t("body.fcBookedHint")}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">{t("body.fcWeighted")}</p>
            <p className="tabular text-2xl font-semibold">
              {formatMoney(forecast.weighted_pipeline)}
            </p>
            <p className="text-xs text-muted-foreground">{t("body.fcWeightedHint")}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">{t("body.fcProjected")}</p>
            <p className="tabular text-2xl font-semibold">
              {formatMoney(forecast.projected)}
            </p>
            {change === null ? (
              // No comparison rather than a misleading one: a percentage
              // against a period that booked nothing is undefined.
              <p className="text-xs text-muted-foreground">{t("body.fcNoCompare")}</p>
            ) : (
              <p
                className={`flex items-center gap-1 text-xs ${change >= 0 ? "text-success" : "text-destructive"}`}
              >
                <ChangeIcon className="size-3" />
                {t("body.fcVsWindow", { pct: Math.abs(change).toFixed(1) })}
              </p>
            )}
          </div>
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{t("body.fcBookedShare")}</span>
            <span className="tabular">{bookedShare.toFixed(0)}%</span>
          </div>
          <Progress value={bookedShare} className="h-2" />
        </div>

        {forecast.breakdown.length ? (
          <div className="space-y-0 border-t pt-2">
            {forecast.breakdown.map((row, index) => (
              <div
                key={row.label}
                className={`flex items-center justify-between gap-3 py-2 ${index > 0 ? "border-t" : ""}`}
              >
                <span className="min-w-0 truncate text-sm">
                  {stageLabel(t, row.label)}
                </span>
                <span className="tabular shrink-0 text-sm font-medium">
                  {formatMoney(row.weighted)}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
