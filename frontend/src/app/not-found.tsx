import Link from "next/link";
import { ArrowLeft, Compass } from "lucide-react";

import { BrandLockup } from "@/components/shared/brand";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-6 p-6 text-center">
      <BrandLockup />
      <span className="grid size-12 place-items-center rounded-xl bg-muted text-muted-foreground">
        <Compass className="size-5" />
      </span>
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          This page isn&apos;t on the map
        </h1>
        <p className="max-w-sm text-sm text-muted-foreground">
          The route you followed doesn&apos;t exist in this prototype.
        </p>
      </div>
      <Button render={<Link href="/dashboard" />}>
        <ArrowLeft className="size-4" />
        Back to dashboard
      </Button>
    </div>
  );
}
