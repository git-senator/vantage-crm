"use client";

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Loader2,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { getInsights, getScore } from "@/lib/api/lead-intelligence-client";
import { useTranslation } from "@/i18n/language-provider";
import type { LeadScoreDetail } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * The lead-intelligence panel.
 *
 * Two halves, mirroring the backend. The **score** is deterministic and shown
 * with its full explanation — every number here is the sum of the reasons below
 * it, so nothing is a black box the agent has to trust. The **AI narrative** is
 * behind a button, because it costs, and it only ever explains the score the
 * rules already produced.
 */

const TEMPERATURE_TONE: Record<string, string> = {
  hot: "text-destructive",
  warm: "text-amber-600 dark:text-amber-500",
  cold: "text-muted-foreground",
};

const PRIORITY_VARIANT: Record<string, "destructive" | "secondary" | "outline"> =
  {
    high: "destructive",
    medium: "secondary",
    low: "outline",
  };

const TEMP_KEY: Record<string, string> = {
  hot: "leadIntel.tempHot",
  warm: "leadIntel.tempWarm",
  cold: "leadIntel.tempCold",
};
const QUAL_KEY: Record<string, string> = {
  qualified: "leadIntel.qualQualified",
  nurture: "leadIntel.qualNurture",
  unqualified: "leadIntel.qualUnqualified",
};
const PRIO_KEY: Record<string, string> = {
  high: "leadIntel.prioHigh",
  medium: "leadIntel.prioMedium",
  low: "leadIntel.prioLow",
};
const INTENT_KEY: Record<string, string> = {
  strong: "leadIntel.intentStrong",
  moderate: "leadIntel.intentModerate",
  weak: "leadIntel.intentWeak",
  none: "leadIntel.intentNone",
};

export function LeadIntelligence({ leadId }: { leadId: string }) {
  const { t } = useTranslation();
  const [score, setScore] = useState<LeadScoreDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [narrative, setNarrative] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    let active = true;
    getScore(leadId)
      .then((result) => active && setScore(result))
      .catch(() => active && toast.error(t("leadIntel.scoreError")))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [leadId, t]);

  async function generate() {
    setGenerating(true);
    try {
      const result = await getInsights(leadId);
      setScore(result.score);
      setNarrative(result.narrative);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t("leadIntel.insightsError"),
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
            <TrendingUp className="size-4" />
            {t("leadIntel.title")}
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

  if (!score) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TrendingUp className="size-4" />
          {t("leadIntel.title")}
        </CardTitle>
        <CardDescription>{t("leadIntel.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* -------------------------------------------------- headline */}
        <div className="space-y-2">
          <div className="flex items-baseline justify-between">
            <span className="text-3xl font-semibold tabular">{score.score}</span>
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "text-sm font-medium",
                  TEMPERATURE_TONE[score.temperature],
                )}
              >
                {t(TEMP_KEY[score.temperature] ?? "")}
              </span>
              <Badge variant={PRIORITY_VARIANT[score.priority] ?? "outline"}>
                {t(PRIO_KEY[score.priority] ?? "")} {t("leadIntel.priorityWord")}
              </Badge>
            </div>
          </div>
          <Progress value={score.score} className="h-1.5" />
          <div className="flex flex-wrap gap-2 pt-1 text-xs text-muted-foreground">
            <span>{t(QUAL_KEY[score.qualification] ?? "")}</span>
            <span>·</span>
            <span>
              {t("leadIntel.buyingIntent")}: {t(INTENT_KEY[score.buying_intent] ?? "")}
            </span>
          </div>
        </div>

        {/* --------------------------------------------------- signals */}
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">
            {t("leadIntel.whatDrove")}
          </p>
          {score.signals.map((signal) => (
            <div
              key={signal.key}
              className="flex items-start justify-between gap-3 text-sm"
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

        {/* ----------------------------------------------------- risks */}
        {score.risks.length > 0 ? (
          <div className="space-y-1.5">
            <p className="flex items-center gap-1.5 text-xs font-medium text-destructive">
              <AlertTriangle className="size-3.5" />
              {t("leadIntel.risks")}
            </p>
            {score.risks.map((risk) => (
              <p key={risk.key} className="text-sm">
                <span className="font-medium">{risk.label}.</span>{" "}
                <span className="text-muted-foreground">{risk.detail}</span>
              </p>
            ))}
          </div>
        ) : null}

        {/* ------------------------------------------- recommendations */}
        {score.recommendations.length > 0 ? (
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground">
              {t("leadIntel.nextSteps")}
            </p>
            {score.recommendations.map((rec) => (
              <div key={rec.action} className="text-sm">
                <span className="font-medium">{rec.action}</span>
                <span className="text-muted-foreground"> — {rec.reason}</span>
              </div>
            ))}
          </div>
        ) : null}

        {/* --------------------------------------------- missing info */}
        {score.missing_info.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {score.missing_info.map((field) => (
              <Badge key={field.key} variant="outline" className="font-normal">
                {t("leadIntel.missing")}: {field.label}
              </Badge>
            ))}
          </div>
        ) : null}

        {/* ------------------------------------------------ narrative */}
        <div className="border-t pt-4">
          {narrative ? (
            <div className="space-y-2">
              <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <Sparkles className="size-3.5" />
                {t("leadIntel.aiSummary")}
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
              {t("leadIntel.generate")}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
