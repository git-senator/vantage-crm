import Link from "next/link";
import { ArrowLeft, Compass } from "lucide-react";

import { BrandLockup } from "@/components/shared/brand";
import { Button } from "@/components/ui/button";
import { getTranslations } from "@/i18n/server";

export default async function NotFound() {
  const t = await getTranslations();

  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-6 p-6 text-center">
      <BrandLockup />
      <span className="grid size-12 place-items-center rounded-xl bg-muted text-muted-foreground">
        <Compass className="size-5" />
      </span>
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          {t("errors.notFoundTitle")}
        </h1>
        <p className="max-w-sm text-sm text-muted-foreground">
          {t("errors.notFoundBody")}
        </p>
      </div>
      <Button render={<Link href="/dashboard" />}>
        <ArrowLeft className="size-4" />
        {t("errors.backToDashboard")}
      </Button>
    </div>
  );
}
