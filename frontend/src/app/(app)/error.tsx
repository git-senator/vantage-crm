"use client";

import { useEffect } from "react";
import { RotateCw, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useTranslation } from "@/i18n/language-provider";

/**
 * Error boundary for the authenticated shell.
 *
 * Shows a recoverable failure rather than bouncing to the login page. A
 * backend outage is not the same as a signed-out session, and treating it as
 * one sends users to re-enter credentials that were never the problem.
 *
 * `digest` is the server-side correlation id — the only detail surfaced,
 * because a stack trace in the browser is reconnaissance.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const { t } = useTranslation();

  useEffect(() => {
    console.error("Application error:", error.message);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-5 text-center">
      <span className="grid size-12 place-items-center rounded-xl bg-destructive/10 text-destructive">
        <TriangleAlert className="size-5" />
      </span>
      <div className="space-y-2">
        <h1 className="text-xl font-semibold tracking-tight">
          {t("errors.genericTitle")}
        </h1>
        <p className="max-w-md text-sm text-muted-foreground">
          {t("errors.genericBody")}
        </p>
        {error.digest && (
          <p className="pt-1 font-mono text-xs text-muted-foreground">
            {t("errors.reference", { id: error.digest })}
          </p>
        )}
      </div>
      <Button onClick={reset} variant="outline">
        <RotateCw className="size-4" />
        {t("buttons.retry")}
      </Button>
    </div>
  );
}
