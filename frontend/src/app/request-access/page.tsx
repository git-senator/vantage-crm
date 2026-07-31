import Link from "next/link";
import type { Metadata } from "next";
import { ArrowLeft } from "lucide-react";

import { RequestAccessForm } from "@/components/auth/request-access-form";
import { BrandLockup } from "@/components/shared/brand";
import { getTranslations } from "@/i18n/server";

export const metadata: Metadata = { title: "Request access" };

export default async function RequestAccessPage() {
  const t = await getTranslations();

  return (
    <div className="flex min-h-svh flex-col px-6 py-8 sm:px-10">
      <header className="flex items-center justify-between">
        <BrandLockup />
        <Link
          href="/login"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          <ArrowLeft className="size-4" />
          {t("access.backToSignIn")}
        </Link>
      </header>

      <div className="flex flex-1 items-center justify-center py-12">
        <div className="w-full max-w-sm">
          <div className="space-y-2">
            <h1 className="text-2xl font-semibold tracking-tight">
              {t("access.requestTitle")}
            </h1>
            <p className="text-sm text-muted-foreground">
              {t("access.requestSubtitle")}
            </p>
          </div>

          <RequestAccessForm />
        </div>
      </div>
    </div>
  );
}
