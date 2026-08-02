import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { RunStatusBadge } from "@/components/automations/run-status-badge";
import { WorkflowBuilder } from "@/components/automations/workflow-builder";
import { WorkflowToggle } from "@/components/automations/workflow-toggle";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getRegistries, getWorkflow, listRuns } from "@/lib/api/automations";
import { getTranslations } from "@/i18n/server";
import { requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Workflow" };

/**
 * The builder, plus this workflow's own run history.
 *
 * Opens the **draft** when there is one and the published version otherwise —
 * the thing you are editing, not the thing that is live. The distinction is
 * shown rather than implied, because publishing is what makes a change reach
 * customers and somebody needs to know which they are looking at.
 */
export default async function WorkflowPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const session = await requireSession();
  const t = await getTranslations();
  const canManage = session.permissions.includes("automations.manage");

  const [detail, registries, runs] = await Promise.all([
    getWorkflow(id).catch(() => null),
    getRegistries(),
    listRuns({ workflow_id: id, limit: 20 }),
  ]);

  if (!detail) notFound();

  const editing = detail.draft ?? detail.published;

  return (
    <div className="space-y-6">
      <PageHeader
        title={detail.workflow.name}
        description={detail.workflow.description ?? undefined}
        actions={
          <div className="flex items-center gap-3">
            {detail.draft && (
              <Badge variant="outline">{t("body.autoUnpublishedDraft")}</Badge>
            )}
            {detail.published && (
              <Badge variant="secondary">
                {t("body.autoLiveVersion", {
                  version: detail.published.version,
                })}
              </Badge>
            )}
            <WorkflowToggle
              workflowId={detail.workflow.id}
              isEnabled={detail.workflow.is_enabled}
              canManage={canManage && detail.workflow.published_version_id !== null}
            />
          </div>
        }
      />

      <WorkflowBuilder
        workflowId={detail.workflow.id}
        initialDefinition={editing?.definition ?? {}}
        registries={registries}
        canManage={canManage}
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">{t("body.autoRunHistory")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("body.autoNotRunYet")}
            </p>
          ) : (
            runs.map((run) => (
              <Link
                key={run.id}
                href={`/automations/runs/${run.id}`}
                className="flex items-center gap-3 rounded-lg border p-2 text-xs transition-colors hover:bg-muted/50"
              >
                <RunStatusBadge status={run.status} />
                <span className="min-w-0 flex-1 truncate text-muted-foreground">
                  {new Date(run.created_at).toLocaleString()}
                  {run.error && ` · ${run.error}`}
                </span>
              </Link>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
