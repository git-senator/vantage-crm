import { notFound, redirect } from "next/navigation";
import type { Metadata } from "next";

import { DealForm } from "@/components/deals/deal-form";
import { PageHeader } from "@/components/shared/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { listClients } from "@/lib/api/clients";
import { getDeal } from "@/lib/api/deals";
import { listProperties } from "@/lib/api/properties";
import { ApiError } from "@/lib/api/server";
import { getTranslations } from "@/i18n/server";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Edit deal" };

export default async function EditDealPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const t = await getTranslations();

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
        title={t("body.cfEditTitle", { name: deal.title })}
        description={t("body.dealEditAudit")}
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
