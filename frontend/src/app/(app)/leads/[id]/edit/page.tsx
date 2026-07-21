import { notFound, redirect } from "next/navigation";
import type { Metadata } from "next";

import { LeadForm } from "@/components/leads/lead-form";
import { PageHeader } from "@/components/shared/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { getLead } from "@/lib/api/leads";
import { ApiError } from "@/lib/api/server";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Edit lead" };

export default async function EditLeadPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;

  if (!hasPermission(session, "leads.manage")) {
    redirect(`/leads/${id}`);
  }

  let lead;
  try {
    lead = await getLead(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={`Edit ${lead.full_name}`}
        description="Changes are recorded in the audit log."
      />
      <Card className="max-w-3xl">
        <CardContent className="pt-6">
          <LeadForm lead={lead} />
        </CardContent>
      </Card>
    </div>
  );
}
