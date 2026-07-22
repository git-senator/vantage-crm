import type { Metadata } from "next";
import Link from "next/link";
import { Workflow as WorkflowIcon } from "lucide-react";

import { NewWorkflowButton } from "@/components/automations/new-workflow-button";
import { RunStatusBadge } from "@/components/automations/run-status-badge";
import { WorkflowToggle } from "@/components/automations/workflow-toggle";
import { EmptyState } from "@/components/shared/empty-state";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getRegistries,
  listRuns,
  listWorkflows,
} from "@/lib/api/automations";
import { requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Workflows" };

/**
 * The automation list and the recent run log side by side.
 *
 * The log is on this page rather than buried inside a workflow because the
 * question people arrive with is "what has my CRM been doing", not "show me
 * workflow #4's history".
 */
export default async function AutomationsPage() {
  const session = await requireSession();
  const canManage = session.permissions.includes("automations.manage");

  const [workflows, runs, registries] = await Promise.all([
    listWorkflows(),
    listRuns({ limit: 15 }),
    getRegistries(),
  ]);

  const triggerLabel = (key: string | null) =>
    registries.triggers.find((entry) => entry.key === key)?.label ?? key ?? "—";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Workflows"
        description="Automations that react to what happens in your CRM."
        actions={
          canManage ? <NewWorkflowButton triggers={registries.triggers} /> : undefined
        }
      />

      <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
        <div className="min-w-0 space-y-3">
          {workflows.length === 0 ? (
            <EmptyState
              icon={WorkflowIcon}
              title="No workflows yet"
              description="Create one to follow up on new leads, chase stalled deals, or file inbound enquiries automatically."
            />
          ) : (
            workflows.map((workflow) => (
              <Card key={workflow.id} className="p-4">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <Link
                      href={`/automations/${workflow.id}`}
                      className="text-sm font-medium hover:underline"
                    >
                      {workflow.name}
                    </Link>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      When: {triggerLabel(workflow.trigger_type)}
                    </p>
                    {workflow.description && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {workflow.description}
                      </p>
                    )}
                  </div>
                  {workflow.published_version_id === null && (
                    <Badge variant="outline">Draft only</Badge>
                  )}
                  <WorkflowToggle
                    workflowId={workflow.id}
                    isEnabled={workflow.is_enabled}
                    canManage={canManage && workflow.published_version_id !== null}
                  />
                </div>
              </Card>
            ))
          )}
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Recent runs</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {runs.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Nothing has run yet.
              </p>
            ) : (
              runs.map((run) => (
                <Link
                  key={run.id}
                  href={`/automations/runs/${run.id}`}
                  className="flex items-center gap-2 rounded-lg border p-2 text-xs transition-colors hover:bg-muted/50"
                >
                  <RunStatusBadge status={run.status} />
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {new Date(run.created_at).toLocaleString()}
                  </span>
                </Link>
              ))
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
