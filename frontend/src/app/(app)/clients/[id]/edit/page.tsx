import { notFound, redirect } from "next/navigation";
import type { Metadata } from "next";

import { ClientForm } from "@/components/clients/client-form";
import { PageHeader } from "@/components/shared/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { getClient } from "@/lib/api/clients";
import { ApiError } from "@/lib/api/server";
import { getTranslations } from "@/i18n/server";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Edit client" };

export default async function EditClientPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const t = await getTranslations();

  if (!hasPermission(session, "contacts.manage")) {
    redirect(`/clients/${id}`);
  }

  let client;
  try {
    client = await getClient(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.cfEditTitle", { name: client.display_name })}
        description={t("body.formEditAudit")}
      />
      <Card className="max-w-3xl">
        <CardContent className="pt-6">
          <ClientForm client={client} />
        </CardContent>
      </Card>
    </div>
  );
}
