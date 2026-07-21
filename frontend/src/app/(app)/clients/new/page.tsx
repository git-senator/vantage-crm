import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { ClientForm } from "@/components/clients/client-form";
import { PageHeader } from "@/components/shared/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "New client" };

export default async function NewClientPage() {
  const session = await requireSession();

  // Belt and braces. The list page hides the button and the API refuses the
  // POST, but someone typing the URL should not reach a form they cannot
  // submit.
  if (!hasPermission(session, "contacts.manage")) {
    redirect("/clients");
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="New client"
        description="Add someone you represent, as a person or a company."
      />
      <Card className="max-w-3xl">
        <CardContent className="pt-6">
          <ClientForm />
        </CardContent>
      </Card>
    </div>
  );
}
