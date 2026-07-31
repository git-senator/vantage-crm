import { notFound } from "next/navigation";
import type { Metadata } from "next";

import { RequestsQueue } from "@/components/access/requests-queue";
import { PageHeader } from "@/components/shared/page-header";
import { getTranslations } from "@/i18n/server";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Access requests" };

export default async function RequestsPage() {
  const session = await requireSession();

  // Managing members is the gate. Anyone without it has no business seeing who
  // asked to join — a 404 rather than a 403 so the route's existence is not
  // even confirmed. The API enforces the same permission regardless.
  if (!hasPermission(session, "users.manage")) {
    notFound();
  }

  const t = await getTranslations();

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("access.queueTitle")}
        description={t("access.queueSubtitle")}
      />
      <RequestsQueue />
    </div>
  );
}
