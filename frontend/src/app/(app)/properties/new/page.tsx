import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { PropertyForm } from "@/components/properties/property-form";
import { PageHeader } from "@/components/shared/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "New listing" };

export default async function NewPropertyPage() {
  const session = await requireSession();

  // Belt and braces. The list page hides the button and the API refuses the
  // POST, but someone typing the URL should not reach a form they cannot
  // submit.
  if (!hasPermission(session, "properties.manage")) {
    redirect("/properties");
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="New listing"
        description="Add a property to the brokerage's inventory."
      />
      <Card className="max-w-3xl">
        <CardContent className="pt-6">
          <PropertyForm />
        </CardContent>
      </Card>
    </div>
  );
}
