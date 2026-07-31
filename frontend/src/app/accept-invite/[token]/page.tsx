import type { Metadata } from "next";

import { AcceptInviteForm } from "@/components/auth/accept-invite-form";
import { BrandLockup } from "@/components/shared/brand";

export const metadata: Metadata = { title: "Accept invitation" };

export default async function AcceptInvitePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;

  return (
    <div className="flex min-h-svh flex-col px-6 py-8 sm:px-10">
      <header>
        <BrandLockup />
      </header>

      <div className="flex flex-1 items-center justify-center py-12">
        <div className="w-full max-w-sm">
          <AcceptInviteForm token={token} />
        </div>
      </div>
    </div>
  );
}
