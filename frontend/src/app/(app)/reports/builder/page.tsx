import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { ReportBuilder } from "@/components/reports/report-builder";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { listDatasets, listReports, listRuns } from "@/lib/api/reports";

export const metadata: Metadata = { title: "Report builder" };

export default async function ReportBuilderPage() {
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
        title="Report builder"
        description="Pick a dataset, filter it, group it, and export. Rows always follow your own access."
        actions={
          <Button variant="outline" render={<Link href="/reports" />}>
            <ArrowLeft className="size-4" />
            Back to analytics
          </Button>
        }
      />
      <ReportBuilder datasets={datasets} saved={saved} runs={runs} />
    </div>
  );
}
