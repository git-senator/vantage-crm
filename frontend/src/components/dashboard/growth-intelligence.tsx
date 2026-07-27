"use client";

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Gauge,
  Loader2,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { getBriefing, getGrowth } from "@/lib/api/growth-intelligence-client";
import type { GrowthHealthDetail } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * The growth-intelligence panel — the workspace's business health.
 *
 * One explainable number, computed by the CRM over the Analytics Engine's own
 * aggregates: a growth score that is the sum of the signals shown. The AI
 * briefing, behind a button, only explains it. Nothing here produces a number.
 */

const BAND_TONE: Record<string, string> = {
  thriving: "text-success",
  steady: "text-success",
  at_risk: "text-amber-600 dark:text-amber-500",
  struggling: "text-destructive",
};

export function GrowthIntelligence() {
  const [growth, setGrowth] = useState<GrowthHealthDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [narrative, setNarrative] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    let active = true;
    getGrowth()
      .then((result) => active && setGrowth(result))
      .catch(() => active && toast.error("Could not analyse growth."))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  async function generate() {
    setGenerating(true);
    try {
      const result = await getBriefing();
      setGrowth(result.growth);
      setNarrative(result.narrative);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not generate briefing.",
      );
    } finally {
      setGenerating(false);
    }
  }

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="size-4 text-primary" />
            Business health
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

  if (!growth) return null;

  return (
    <Card className="gap-0 overflow-hidden py-0">
      <CardHeader className="border-b py-4">
        <CardTitle className="flex items-center gap-2 text-base">
          <Sparkles className="size-4 text-primary" />
          Business health
        </CardTitle>
        <CardDescription>
          Computed from your analytics for {growth.period_label} — the score is
          the sum of the reasons below it.
        </CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="sm"
            onClick={generate}
            disabled={generating}
          >
            {generating ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Sparkles className="size-4" />
            )}
            Generate briefing
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-px bg-border p-0 md:grid-cols-3">
        {/* ---------------------------------------------------- score */}
        <div className="space-y-3 bg-card p-5">
          <div className="flex items-center gap-2">
            <Gauge className="size-4 text-muted-foreground" />
            <span className="text-xs text-muted-foreground">Growth score</span>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-semibold tabular">
              {growth.score}
            </span>
            <span
              className={cn(
                "text-xs font-medium capitalize",
                BAND_TONE[growth.band],
              )}
            >
              {growth.band.replace("_", " ")}
            </span>
          </div>
          <Progress value={growth.score} className="h-1.5" />
          <div className="space-y-1 pt-1">
            {growth.signals
              .slice()
              .sort((a, b) => Math.abs(b.points) - Math.abs(a.points))
              .slice(0, 4)
              .map((signal) => (
                <div
                  key={signal.key}
                  className="flex items-start justify-between gap-3 text-xs"
                >
                  <span className="min-w-0 text-muted-foreground">
                    {signal.reason}
                  </span>
                  <span
                    className={cn(
                      "tabular shrink-0 font-medium",
                      signal.points >= 0 ? "text-success" : "text-destructive",
                    )}
                  >
                    {signal.points >= 0 ? "+" : ""}
                    {signal.points}
                  </span>
                </div>
              ))}
          </div>
        </div>

        {/* -------------------------------------- revenue + pipeline */}
        <div className="space-y-3 bg-card p-5">
          <div className="flex items-center gap-2">
            <TrendingUp className="size-4 text-muted-foreground" />
            <span className="text-xs text-muted-foreground">
              Revenue &amp; pipeline
            </span>
          </div>
          {[...growth.revenue_signals, ...growth.pipeline_insights]
            .slice(0, 5)
            .map((line) => (
              <p key={line} className="text-xs leading-relaxed">
                {line}
              </p>
            ))}
        </div>

        {/* ----------------------------------- risks / briefing */}
        <div className="space-y-3 bg-card p-5">
          {narrative ? (
            <>
              <div className="flex items-center gap-2">
                <Sparkles className="size-4 text-primary" />
                <span className="text-xs text-muted-foreground">
                  AI briefing
                </span>
              </div>
              <p className="whitespace-pre-wrap text-xs leading-relaxed">
                {narrative}
              </p>
            </>
          ) : (
            <>
              <div className="flex items-center gap-2">
                <AlertTriangle className="size-4 text-muted-foreground" />
                <span className="text-xs text-muted-foreground">
                  Priorities
                </span>
              </div>
              {growth.risks.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No risks flagged this period.
                </p>
              ) : (
                growth.risks.slice(0, 3).map((risk) => (
                  <p key={risk.key} className="text-xs">
                    <span className="font-medium">{risk.label}.</span>{" "}
                    <span className="text-muted-foreground">{risk.detail}</span>
                  </p>
                ))
              )}
              {growth.recommendations.slice(0, 2).map((rec) => (
                <p
                  key={rec.action}
                  className="flex items-start gap-1.5 text-xs text-muted-foreground"
                >
                  <ArrowRight className="mt-0.5 size-3 shrink-0" />
                  <span>
                    <span className="font-medium text-foreground">
                      {rec.action}
                    </span>{" "}
                    — {rec.reason}
                  </span>
                </p>
              ))}
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
