import { notFound, redirect } from "next/navigation";
import type { Metadata } from "next";

import { DealForm } from "@/components/deals/deal-form";
import { PageHeader } from "@/components/shared/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { listClients } from "@/lib/api/clients";
import { getDeal } from "@/lib/api/deals";
import { listProperties } from "@/lib/api/properties";
import { ApiError } from "@/lib/api/server";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Edit deal" };

export default async function EditDealPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;

  if (!hasPermission(session, "deals.manage")) {
    redirect(`/deals/${id}`);
  }

  let deal;
  try {
    deal = await getDeal(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  const [clients, properties] = await Promise.all([
    listClients({ limit: 100 }),
    listProperties({ limit: 100 }),
  ]);

  return (
    <div className="space-y-6">
      <PageHeader
        title={`Edit ${deal.title}`}
        description="Changes are recorded in the audit log. To move the deal between stages, use Move stage."
      />
      <Card className="max-w-3xl">
        <CardContent className="pt-6">
          <DealForm
            deal={deal}
            clients={clients.data}
            properties={properties.data}
          />
        </CardContent>
      </Card>
    </div>
  );
}
