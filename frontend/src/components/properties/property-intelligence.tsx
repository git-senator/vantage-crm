"use client";

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Gauge,
  Loader2,
  Sparkles,
  Tag,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  generateContent,
  getQuality,
} from "@/lib/api/property-intelligence-client";
import type {
  PropertyContentKind,
  PropertyQualityDetail,
} from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * The property-intelligence panel.
 *
 * Two deterministic numbers, both explainable and both computed by the CRM: a
 * **quality** score (the sum of the signals shown) and a **completeness** reading
 * (how much of the listing checklist is filled). The pricing insight is measured
 * against the market's own comparable median. The generated copy, behind buttons,
 * only markets what the listing already says — nothing here invents a fact.
 */

const GRADE_TONE: Record<string, string> = {
  excellent: "text-success",
  good: "text-success",
  fair: "text-amber-600 dark:text-amber-500",
  poor: "text-destructive",
};

const STANCE_TONE: Record<string, string> = {
  below: "text-success",
  in_line: "text-muted-foreground",
  above: "text-destructive",
  unknown: "text-muted-foreground",
};

const CONTENT_KINDS: { kind: PropertyContentKind; label: string }[] = [
  { kind: "summary", label: "Summary" },
  { kind: "description", label: "Description" },
  { kind: "seo", label: "SEO" },
];

export function PropertyIntelligence({ propertyId }: { propertyId: string }) {
  const [quality, setQuality] = useState<PropertyQualityDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [content, setContent] = useState<{
    kind: PropertyContentKind;
    text: string;
  } | null>(null);
  const [generating, setGenerating] = useState<PropertyContentKind | null>(null);

  useEffect(() => {
    let active = true;
    getQuality(propertyId)
      .then((result) => active && setQuality(result))
      .catch(() => active && toast.error("Could not analyse this listing."))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [propertyId]);

  async function generate(kind: PropertyContentKind) {
    setGenerating(kind);
    try {
      const result = await generateContent(propertyId, kind);
      setQuality(result.quality);
      setContent({ kind, text: result.content });
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Could not generate content.",
      );
    } finally {
      setGenerating(null);
    }
  }

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Gauge className="size-4" />
            Listing intelligence
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

  if (!quality) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Gauge className="size-4" />
          Listing intelligence
        </CardTitle>
        <CardDescription>
          Quality and completeness, computed from the listing and the market&apos;s
          own comparable prices — each number is the sum of the reasons below it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* -------------------------------------------------- headline */}
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <p className="text-xs text-muted-foreground">Quality</p>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular">
                {quality.quality}
              </span>
              <span
                className={cn(
                  "text-xs font-medium capitalize",
                  GRADE_TONE[quality.grade],
                )}
              >
                {quality.grade}
              </span>
            </div>
            <Progress value={quality.quality} className="h-1.5" />
          </div>
          <div className="space-y-1.5">
            <p className="text-xs text-muted-foreground">Completeness</p>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular">
                {quality.completeness}%
              </span>
            </div>
            <Progress value={quality.completeness} className="h-1.5" />
          </div>
        </div>

        {/* ------------------------------------------------- pricing */}
        <div className="space-y-1.5">
          <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Tag className="size-3.5" />
            Pricing insight
          </p>
          <p className="text-sm">
            <span
              className={cn(
                "font-medium capitalize",
                STANCE_TONE[quality.pricing.stance],
              )}
            >
              {quality.pricing.stance.replace("_", " ")}
            </span>
            <span className="text-muted-foreground">
              {" — "}
              {quality.pricing.reason}
            </span>
          </p>
        </div>

        {/* ----------------------------------------------- strengths */}
        {quality.strengths.length > 0 ? (
          <div className="space-y-1.5">
            <p className="flex items-center gap-1.5 text-xs font-medium text-success">
              <CheckCircle2 className="size-3.5" />
              Strengths
            </p>
            {quality.strengths.map((strength) => (
              <p key={strength} className="text-sm text-muted-foreground">
                {strength}
              </p>
            ))}
          </div>
        ) : null}

        {/* ---------------------------------------------- weaknesses */}
        {quality.weaknesses.length > 0 ? (
          <div className="space-y-1.5">
            <p className="flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-500">
              <AlertTriangle className="size-3.5" />
              Weaknesses
            </p>
            {quality.weaknesses.map((weakness) => (
              <p key={weakness} className="text-sm text-muted-foreground">
                {weakness}
              </p>
            ))}
          </div>
        ) : null}

        {/* ------------------------------------------- recommendations */}
        {quality.recommendations.length > 0 ? (
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground">
              Recommended next steps
            </p>
            {quality.recommendations.map((rec) => (
              <div key={rec.action} className="text-sm">
                <span className="font-medium">{rec.action}</span>
                <span className="text-muted-foreground"> — {rec.reason}</span>
              </div>
            ))}
          </div>
        ) : null}

        {/* --------------------------------------------- generation */}
        <div className="space-y-3 border-t pt-4">
          <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Sparkles className="size-3.5" />
            Generate listing content
          </p>
          <div className="flex flex-wrap gap-2">
            {CONTENT_KINDS.map(({ kind, label }) => (
              <Button
                key={kind}
                variant="outline"
                size="sm"
                onClick={() => generate(kind)}
                disabled={generating !== null}
              >
                {generating === kind ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Sparkles className="size-4" />
                )}
                {label}
              </Button>
            ))}
          </div>

          {content ? (
            <div className="space-y-1.5 rounded-md border bg-muted/40 p-3">
              <p className="text-xs font-medium text-muted-foreground capitalize">
                {content.kind}
              </p>
              <p className="whitespace-pre-wrap text-sm leading-relaxed">
                {content.text}
              </p>
            </div>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
