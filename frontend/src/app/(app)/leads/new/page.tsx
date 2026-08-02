import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { LeadForm } from "@/components/leads/lead-form";
import { PageHeader } from "@/components/shared/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { getTranslations } from "@/i18n/server";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "New lead" };

export default async function NewLeadPage() {
  const session = await requireSession();
  const t = await getTranslations();

  // Belt and braces. The list page hides the button and the API refuses the
  // POST, but someone typing the URL should not reach a form they cannot
  // submit.
  if (!hasPermission(session, "leads.manage")) {
    redirect("/leads");
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.newLead")}
        description={t("body.newLeadDesc")}
      />
      <Card className="max-w-3xl">
        <CardContent className="pt-6">
          <LeadForm />
        </CardContent>
      </Card>
    </div>
  );
}
