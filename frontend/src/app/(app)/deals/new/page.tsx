import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { DealForm } from "@/components/deals/deal-form";
import { PageHeader } from "@/components/shared/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { listClients } from "@/lib/api/clients";
import { listProperties } from "@/lib/api/properties";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "New deal" };

export default async function NewDealPage() {
  const session = await requireSession();

  // Belt and braces. The list hides the button and the API refuses the POST,
  // but someone typing the URL should not reach a form they cannot submit.
  if (!hasPermission(session, "deals.manage")) {
    redirect("/deals");
  }

  // Both lists are scoped server-side, so the pickers can only offer records
  // the caller may actually reference.
  const [clients, properties] = await Promise.all([
    listClients({ limit: 100 }),
    listProperties({ limit: 100 }),
  ]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="New deal"
        description="Deals start in the default pipeline's first stage."
      />
      <Card className="max-w-3xl">
        <CardContent className="pt-6">
          <DealForm clients={clients.data} properties={properties.data} />
        </CardContent>
      </Card>
    </div>
  );
}
