import type { Metadata } from "next";
import Link from "next/link";
import { Building2, Mail, Phone, ShieldCheck, ShieldAlert } from "lucide-react";

import { ProfileForm } from "@/components/profile/profile-form";
import { PageHeader } from "@/components/shared/page-header";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { getTranslations } from "@/i18n/server";
import { requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Profile" };

export default async function ProfilePage() {
  const me = await requireSession();
  const t = await getTranslations();
  const avatar = {
    id: me.id,
    name: me.full_name,
    initials: me.initials,
    role: me.job_title ?? t("body.roleMember"),
    hue: me.avatar_hue,
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("body.profTitle")}
        description={t("body.profDesc")}
      />

      {/* ---------------------------------------------------------- hero */}
      <Card className="gap-0 overflow-hidden py-0">
        <div
          className="h-28"
          style={{
            backgroundImage:
              "linear-gradient(120deg, oklch(0.55 0.19 268), oklch(0.62 0.15 210))",
          }}
        />
        <CardContent className="p-6 pt-0">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
            <UserAvatar user={avatar} size="xl" className="-mt-10 ring-4 ring-card" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-xl font-semibold">{me.full_name}</h2>
                <StatusBadge status={me.status} />
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                {me.job_title ?? t("body.roleMember")} · {me.organization.name}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <Mail className="size-3.5" />
                  {me.email}
                </span>
                {me.phone && (
                  <span className="flex items-center gap-1.5">
                    <Phone className="size-3.5" />
                    {me.phone}
                  </span>
                )}
                <span className="flex items-center gap-1.5">
                  <Building2 className="size-3.5" />
                  {me.organization.name}
                </span>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        {/* ------------------------------------------------ editable details */}
        <Card>
          <CardHeader>
            <CardTitle>{t("body.profPersonal")}</CardTitle>
          </CardHeader>
          <CardContent>
            <ProfileForm
              fullName={me.full_name}
              jobTitle={me.job_title}
              phone={me.phone}
            />
          </CardContent>
        </Card>

        {/* --------------------------------------------------------- account */}
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">{t("body.profAccount")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">{t("body.profEmail")}</span>
                <span className="truncate font-medium">{me.email}</span>
              </div>
              <Separator />
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">{t("body.profRoles")}</span>
                <span className="font-medium capitalize">
                  {me.roles.length ? me.roles.join(", ") : "—"}
                </span>
              </div>
              <Separator />
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">{t("body.profWorkspace")}</span>
                <span className="truncate font-medium">
                  {me.organization.name}
                </span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">{t("body.profSecurity")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center gap-2.5 text-sm">
                {me.mfa_enabled ? (
                  <ShieldCheck className="size-4 text-success" />
                ) : (
                  <ShieldAlert className="size-4 text-warning" />
                )}
                <span>
                  {t("body.prof2fa")}{" "}
                  <span className="font-medium">
                    {me.mfa_enabled ? t("body.prof2faOn") : t("body.prof2faOff")}
                  </span>
                </span>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                render={<Link href="/settings" />}
              >
                {t("body.profManageSecurity")}
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
