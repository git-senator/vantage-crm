import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { ReportBuilder } from "@/components/reports/report-builder";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { listDatasets, listReports, listRuns } from "@/lib/api/reports";
import { getTranslations } from "@/i18n/server";

export const metadata: Metadata = { title: "Report builder" };

export default async function ReportBuilderPage() {
  const t = await getTranslations();
  // The catalogue arrives already filtered to what this caller may query, so
  // the builder never offers a dataset that would be refused — and never needs
  // its own copy of the permission matrix to work that out.
  const [datasets, saved, runs] = await Promise.all([
    listDatasets(),
    listReports(),
    listRuns(),
  ]);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.rbTitle")}
        description={t("body.rbDesc")}
        actions={
          <Button variant="outline" render={<Link href="/reports" />}>
            <ArrowLeft className="size-4" />
            {t("body.rbBackToAnalytics")}
          </Button>
        }
      />
      <ReportBuilder datasets={datasets} saved={saved} runs={runs} />
    </div>
  );
}
