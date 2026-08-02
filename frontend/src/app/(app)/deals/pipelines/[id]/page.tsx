import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import type { Metadata } from "next";
import { ArrowLeft } from "lucide-react";

import { PipelineEditor } from "@/components/deals/pipeline-editor";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { getPipeline } from "@/lib/api/deals";
import { ApiError } from "@/lib/api/server";
import { getTranslations } from "@/i18n/server";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Pipeline" };

export default async function PipelinePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const t = await getTranslations();

  // Pipelines are workspace configuration, so this is settings.manage rather
  // than deals.manage — an agent who can edit deals must not be able to delete
  // the stage a colleague's deals sit in. The API enforces it regardless.
  if (!hasPermission(session, "settings.manage")) {
    redirect("/deals");
  }

  let pipeline;
  try {
    pipeline = await getPipeline(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  return (
    <div className="space-y-6">
      <Button
        variant="ghost"
        size="sm"
        className="-ml-2 text-muted-foreground"
        render={<Link href="/deals" />}
      >
        <ArrowLeft className="size-4" />
        {t("body.autoBackToBoard")}
      </Button>

      <PageHeader
        title={pipeline.name}
        description={t("body.peBoardHint")}
      />

      <PipelineEditor pipeline={pipeline} />
    </div>
  );
}
