import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { RunStatusBadge } from "@/components/automations/run-status-badge";
import { PageHeader } from "@/components/shared/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getRun } from "@/lib/api/automations";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Workflow run" };

/**
 * One run's execution log.
 *
 * This page is the answer to "why did this client get that email", which is the
 * question that decides whether an automation feature is operable at all. Step
 * labels are the ones recorded at execution time, so editing the workflow
 * afterwards does not rewrite what the log says happened.
 */
export default async function RunPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const detail = await getRun(id).catch(() => null);
  if (!detail) notFound();

  const { run, steps } = detail;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Workflow run"
        description={`Started ${new Date(run.created_at).toLocaleString()}`}
        actions={<RunStatusBadge status={run.status} />}
      />

      {run.error && (
        <Card className="border-destructive/40 bg-destructive/5 p-4">
          <p className="text-sm text-destructive">{run.error}</p>
        </Card>
      )}

      {run.status === "waiting" && run.resume_at && (
        <Card className="p-4">
          <p className="text-sm text-muted-foreground">
            Waiting until {new Date(run.resume_at).toLocaleString()}.
          </p>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Steps</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {steps.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              This run has not executed a step yet.
            </p>
          ) : (
            steps.map((step) => (
              <div
                key={step.id}
                className={cn(
                  "rounded-lg border p-3",
                  step.status === "failed" && "border-destructive/40 bg-destructive/5",
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="tabular text-xs text-muted-foreground">
                    {step.sequence}
                  </span>
                  <p className="min-w-0 flex-1 truncate text-sm font-medium">
                    {step.node_label || step.node_id}
                  </p>
                  <span className="text-[11px] text-muted-foreground">
                    {step.node_type}
                  </span>
                </div>
                {step.error && (
                  <p className="mt-1 text-xs text-destructive">{step.error}</p>
                )}
                {Object.keys(step.output).length > 0 && (
                  <pre className="mt-1.5 overflow-x-auto rounded bg-muted/50 p-2 text-[11px]">
                    {JSON.stringify(step.output, null, 2)}
                  </pre>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
