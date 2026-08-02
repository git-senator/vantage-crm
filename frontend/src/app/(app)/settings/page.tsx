import type { Metadata } from "next";
import Link from "next/link";
import { CalendarClock, CheckCircle2 } from "lucide-react";

import { LanguageCard } from "@/components/settings/language-card";
import { MfaCard } from "@/components/settings/mfa-card";
import { SecurityActions } from "@/components/settings/security-actions";
import { SettingsNav } from "@/components/settings/settings-nav";
import { TeamMembers } from "@/components/settings/team-members";
import { WorkspaceSettingsForm } from "@/components/settings/workspace-settings-form";
import { PageHeader } from "@/components/shared/page-header";
import { StatusBadge } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getMfaStatus } from "@/lib/api/mfa";
import {
  getIntegrations,
  getOrganization,
  listMembers,
} from "@/lib/api/organization";
import { getTranslations } from "@/i18n/server";
import { hasPermission, requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Settings" };

export default async function SettingsPage() {
  const session = await requireSession();
  const [organization, members, integrations, mfa, t] = await Promise.all([
    getOrganization(),
    listMembers(),
    getIntegrations(),
    getMfaStatus(),
    getTranslations(),
  ]);

  const canManageRoles = hasPermission(session, "roles.manage");
  const canManageUsers = hasPermission(session, "users.manage");

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("settings.title")}
        description={t("settings.description")}
      />

      <div className="grid gap-6 lg:grid-cols-[220px_1fr]">
        <SettingsNav />

        <div className="min-w-0 space-y-6">
          {/* ---------------------------------------- language & region */}
          <div id="language" className="scroll-mt-20">
            <LanguageCard />
          </div>

          {/* --------------------------- workspace details + AI prefs */}
          <WorkspaceSettingsForm organization={organization} />

          {/* ---------------------------------------------------- team */}
          <Card id="team" className="scroll-mt-20 gap-0 overflow-hidden py-0">
            <CardHeader className="border-b py-4">
              <CardTitle>{t("settings.teamRoles")}</CardTitle>
              <CardDescription>
                {t("settings.seatsUsed", { used: members.length, total: 25 })}
              </CardDescription>
              {canManageUsers && (
                <CardAction>
                  <Button size="sm" render={<Link href="/requests" />}>
                    {t("settings.inviteMember")}
                  </Button>
                </CardAction>
              )}
            </CardHeader>
            <CardContent className="p-0">
              <TeamMembers members={members} canManage={canManageRoles} />
            </CardContent>
          </Card>

          {/* -------------------------------------------- integrations */}
          <Card id="integrations" className="scroll-mt-20">
            <CardHeader>
              <CardTitle>{t("settings.integrations")}</CardTitle>
              <CardDescription>{t("settings.integrationsRealDesc")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-start gap-3 rounded-lg border p-3.5">
                <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                  <CalendarClock className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm leading-snug font-medium">
                    {t("settings.calGoogleName")}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {integrations.calendar_connected
                      ? t("settings.calConnectedDetail", {
                          id: integrations.calendar_id ?? "",
                        })
                      : t("settings.calNotConfigured")}
                  </p>
                </div>
                <StatusBadge
                  status={
                    integrations.calendar_connected ? "available" : "pending_upload"
                  }
                  label={
                    integrations.calendar_connected
                      ? t("settings.integrationConnected")
                      : t("settings.integrationNotConnected")
                  }
                />
              </div>

              {/* Honest about what's live vs coming with the channels rollout. */}
              <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <CheckCircle2 className="size-3.5 shrink-0 text-success" />
                {t("settings.calChannelsNote")}
              </p>
            </CardContent>
          </Card>

          {/* ------------------------------------------------ security */}
          <div id="security" className="scroll-mt-20 space-y-6">
            <MfaCard status={mfa} />
            <SecurityActions />
          </div>
        </div>
      </div>
    </div>
  );
}
