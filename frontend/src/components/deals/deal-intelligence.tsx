"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Gauge, Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { getHealth, getInsights } from "@/lib/api/deal-intelligence-client";
import type { DealHealthDetail } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * The deal-intelligence panel.
 *
 * Two numbers, both explainable, both computed by the CRM: an overall **health**
 * (the sum of the signals shown) and an inferred **win probability** (the stage
 * baseline plus the ± factors shown). The AI narrative, behind a button, only
 * explains them. Nothing here produces a number — the frontend renders what the
 * deterministic engine returned.
 */

const STATUS_TONE: Record<string, string> = {
  healthy: "text-success",
  won: "text-success",
  at_risk: "text-amber-600 dark:text-amber-500",
  critical: "text-destructive",
  lost: "text-destructive",
};

export function DealIntelligence({ dealId }: { dealId: string }) {
  const [health, setHealth] = useState<DealHealthDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [narrative, setNarrative] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    let active = true;
    getHealth(dealId)
      .then((result) => active && setHealth(result))
      .catch(() => active && toast.error("Could not analyse this deal."))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [dealId]);

  async function generate() {
    setGenerating(true);
    try {
      const result = await getInsights(dealId);
      setHealth(result.health);
      setNarrative(result.narrative);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not generate insights.",
      );
    } finally {
      setGenerating(false);
    }
  }

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Gauge className="size-4" />
            Deal intelligence
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex justify-center py-6">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!health) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Gauge className="size-4" />
          Deal intelligence
        </CardTitle>
        <CardDescription>
          Health and win probability, computed from the deal and the pipeline&apos;s
          own pace — each number is the sum of the reasons below it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* -------------------------------------------------- headline */}
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <p className="text-xs text-muted-foreground">Health</p>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular">
                {health.health}
              </span>
              <span
                className={cn(
                  "text-xs font-medium capitalize",
                  STATUS_TONE[health.status],
                )}
              >
                {health.status.replace("_", " ")}
              </span>
            </div>
            <Progress value={health.health} className="h-1.5" />
          </div>
          <div className="space-y-1.5">
            <p className="text-xs text-muted-foreground">Win probability</p>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular">
                {health.win_probability}%
              </span>
              {health.is_stalled ? (
                <Badge variant="destructive">stalled</Badge>
              ) : null}
            </div>
            <Progress value={health.win_probability} className="h-1.5" />
          </div>
        </div>

        {health.forecast_value ? (
          <p className="text-xs text-muted-foreground">
            Weighted forecast contribution:{" "}
            <span className="tabular font-medium text-foreground">
              ${Number(health.forecast_value).toLocaleString()}
            </span>
          </p>
        ) : null}

        {/* ----------------------------------- win probability factors */}
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">
            How the win probability was reached
          </p>
          {health.probability_factors.map((factor) => (
            <div
              key={factor.key}
              className="flex items-start justify-between gap-3 text-sm"
            >
              <span className="min-w-0 text-muted-foreground">
                {factor.reason}
              </span>
              <span
                className={cn(
                  "tabular shrink-0 font-medium",
                  factor.points >= 0 ? "text-success" : "text-destructive",
                )}
              >
                {factor.points >= 0 ? "+" : ""}
                {factor.points}
              </span>
            </div>
          ))}
        </div>

        {/* ----------------------------------------------------- risks */}
        {health.risks.length > 0 ? (
          <div className="space-y-1.5">
            <p className="flex items-center gap-1.5 text-xs font-medium text-destructive">
              <AlertTriangle className="size-3.5" />
              Risks
            </p>
            {health.risks.map((risk) => (
              <p key={risk.key} className="text-sm">
                <span className="font-medium">{risk.label}.</span>{" "}
                <span className="text-muted-foreground">{risk.detail}</span>
              </p>
            ))}
          </div>
        ) : null}

        {/* ------------------------------------------- recommendations */}
        {health.recommendations.length > 0 ? (
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground">
              Recommended next steps
            </p>
            {health.recommendations.map((rec) => (
              <div key={rec.action} className="text-sm">
                <span className="font-medium">{rec.action}</span>
                <span className="text-muted-foreground"> — {rec.reason}</span>
              </div>
            ))}
          </div>
        ) : null}

        {/* ------------------------------------------------ narrative */}
        <div className="border-t pt-4">
          {narrative ? (
            <div className="space-y-2">
              <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <Sparkles className="size-3.5" />
                AI summary
              </p>
              <p className="whitespace-pre-wrap text-sm leading-relaxed">
                {narrative}
              </p>
            </div>
          ) : (
            <Button
              variant="outline"
              size="sm"
              onClick={generate}
              disabled={generating}
              className="w-full"
            >
              {generating ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Sparkles className="size-4" />
              )}
              Generate AI summary
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
