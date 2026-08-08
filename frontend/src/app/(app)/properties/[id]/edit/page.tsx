import { notFound, redirect } from "next/navigation";
import type { Metadata } from "next";

import { PropertyForm } from "@/components/properties/property-form";
import { PageHeader } from "@/components/shared/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { getProperty } from "@/lib/api/properties";
import { ApiError } from "@/lib/api/server";
import { getTranslations } from "@/i18n/server";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Edit listing" };

export default async function EditPropertyPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const t = await getTranslations();

  if (!hasPermission(session, "properties.manage")) {
    redirect(`/properties/${id}`);
  }

  let property;
  try {
    property = await getProperty(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  // Shared inventory: an agent can reach this URL for a colleague's listing,
  // because they can genuinely see it. Bounce them to the detail page rather
  // than rendering a form whose every submission would 403.
  const isMine = property.listing_agent?.id === session.id;
  if (!isMine && !hasPermission(session, "properties.assign")) {
    redirect(`/properties/${id}`);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.cfEditTitle", { name: property.title })}
        description={t("body.formEditAudit")}
      />
      <Card className="max-w-3xl">
        <CardContent className="pt-6">
          <PropertyForm
            property={property}
            units={session.organization.measurement_system}
          />
        </CardContent>
      </Card>
    </div>
  );
}
