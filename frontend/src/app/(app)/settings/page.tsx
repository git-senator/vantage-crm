import type { Metadata } from "next";
import {
  Building2,
  CreditCard,
  Globe,
  KeyRound,
  Plug,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserCog,
  Users,
} from "lucide-react";

import { LanguageCard } from "@/components/settings/language-card";
import { MfaCard } from "@/components/settings/mfa-card";
import { PageHeader } from "@/components/shared/page-header";
import { StatusBadge } from "@/components/shared/status-badge";
import { UserAvatar } from "@/components/shared/user-avatar";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { getTranslations } from "@/i18n/server";
import { team } from "@/lib/mock-data";
import { getMfaStatus } from "@/lib/api/mfa";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Settings" };

const members = [
  { member: team.avery, access: "Owner", status: "active" },
  { member: team.marcus, access: "Admin", status: "active" },
  { member: team.priya, access: "Member", status: "active" },
  { member: team.jonah, access: "Member", status: "active" },
  { member: team.sofia, access: "Member", status: "active" },
  { member: team.dmitri, access: "Limited", status: "dormant" },
];

const integrations = [
  { name: "MLS / Bay Area Real Estate Information Services", detail: "Listing sync every 15 minutes", connected: true },
  { name: "DocuSign", detail: "E-signature requests and status", connected: true },
  { name: "Google Calendar", detail: "Two-way event sync", connected: true },
  { name: "Zillow Premier Agent", detail: "Inbound lead capture", connected: true },
  { name: "Mailchimp", detail: "Drip campaigns for past clients", connected: false },
  { name: "QuickBooks", detail: "Commission reconciliation", connected: false },
];

export default async function SettingsPage() {
  const [mfa, t] = await Promise.all([getMfaStatus(), getTranslations()]);

  const sections = [
    { icon: UserCog, label: t("settings.sectionGeneral") },
    { icon: Building2, label: t("settings.sectionWorkspace") },
    { icon: Users, label: t("settings.sectionTeam") },
    { icon: Sparkles, label: t("settings.sectionAi") },
    { icon: Plug, label: t("settings.sectionIntegrations") },
    { icon: ShieldCheck, label: t("settings.sectionSecurity") },
    { icon: CreditCard, label: t("settings.sectionBilling") },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("settings.title")}
        description={t("settings.description")}
      />

      <div className="grid gap-6 lg:grid-cols-[220px_1fr]">
        {/* ----------------------------------------------------- section nav */}
        <nav className="lg:sticky lg:top-20 lg:self-start">
          <ul className="flex gap-1 overflow-x-auto lg:flex-col lg:overflow-visible">
            {sections.map((section, index) => (
              <li key={section.label}>
                <button
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm whitespace-nowrap transition-colors",
                    index === 0
                      ? "bg-accent font-medium text-accent-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  <section.icon className="size-4 shrink-0" />
                  {section.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="min-w-0 space-y-6">
          {/* ------------------------------------------ language & region */}
          <LanguageCard />

          {/* ------------------------------------------------------ general */}
          <Card>
            <CardHeader>
              <CardTitle>{t("settings.workspaceDetails")}</CardTitle>
              <CardDescription>
                {t("settings.workspaceDetailsDesc")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="org">{t("settings.brokerageName")}</Label>
                  <Input id="org" defaultValue="Vantage Realty Group" />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="license">
                    {t("settings.brokerageLicense")}
                  </Label>
                  <Input id="license" defaultValue="CA DRE #02114876" />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="timezone">{t("settings.timezone")}</Label>
                  <Select defaultValue="Pacific Time (US & Canada)">
                    <SelectTrigger id="timezone">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="Pacific Time (US & Canada)">Pacific Time (US & Canada)</SelectItem>
                      <SelectItem value="Mountain Time">Mountain Time</SelectItem>
                      <SelectItem value="Central Time">Central Time</SelectItem>
                      <SelectItem value="Eastern Time">Eastern Time</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="currency">{t("settings.currency")}</Label>
                  <Select defaultValue="USD ($)">
                    <SelectTrigger id="currency">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="USD ($)">USD ($)</SelectItem>
                      <SelectItem value="CAD ($)">CAD ($)</SelectItem>
                      <SelectItem value="EUR (€)">EUR (€)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <Separator />

              <div className="space-y-4">
                {[
                  { label: "Weekend notifications", detail: "Send lead alerts on Saturday and Sunday.", on: false },
                  { label: "Auto-assign inbound leads", detail: "Route new leads by round robin within the team.", on: true },
                  { label: "Require deal approval over $5M", detail: "Managing broker signs off before an offer is submitted.", on: true },
                ].map((row) => (
                  <div key={row.label} className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <Label className="text-sm">{row.label}</Label>
                      <p className="mt-0.5 text-sm text-muted-foreground">
                        {row.detail}
                      </p>
                    </div>
                    <Switch defaultChecked={row.on} aria-label={row.label} />
                  </div>
                ))}
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <Button variant="ghost">{t("buttons.cancel")}</Button>
                <Button>{t("buttons.saveChanges")}</Button>
              </div>
            </CardContent>
          </Card>

          {/* --------------------------------------------------------- AI */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Sparkles className="size-4 text-primary" />
                {t("settings.aiPreferences")}
              </CardTitle>
              <CardDescription>
                {t("settings.aiPreferencesDesc")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="space-y-3">
                <div className="flex items-baseline justify-between">
                  <Label>Lead score threshold for alerts</Label>
                  <span className="tabular text-sm font-medium">80</span>
                </div>
                <Slider defaultValue={[80]} max={100} step={5} />
                <p className="text-sm text-muted-foreground">
                  You&apos;ll be notified the moment a lead crosses this score.
                </p>
              </div>

              <Separator />

              <div className="space-y-2">
                <Label htmlFor="tone">{t("settings.draftingTone")}</Label>
                <Select defaultValue="Professional">
                  <SelectTrigger id="tone" className="sm:w-64">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="Professional">Professional</SelectItem>
                    <SelectItem value="Warm and conversational">Warm and conversational</SelectItem>
                    <SelectItem value="Concise">Concise</SelectItem>
                    <SelectItem value="Formal">Formal</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-4">
                {[
                  { label: "Suggest replies in Messages", detail: "Draft three options under each incoming message.", on: true },
                  { label: "Daily priority briefing", detail: "A ranked call list in your inbox at 7:30am.", on: true },
                  { label: "Auto-summarise documents", detail: "Generate a plain-language summary when a file is uploaded.", on: false },
                ].map((row) => (
                  <div key={row.label} className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <Label className="text-sm">{row.label}</Label>
                      <p className="mt-0.5 text-sm text-muted-foreground">
                        {row.detail}
                      </p>
                    </div>
                    <Switch defaultChecked={row.on} aria-label={row.label} />
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* ------------------------------------------------------- team */}
          <Card className="gap-0 overflow-hidden py-0">
            <CardHeader className="border-b py-4">
              <CardTitle>{t("settings.teamRoles")}</CardTitle>
              <CardDescription>
                {t("settings.seatsUsed", { used: 6, total: 25 })}
              </CardDescription>
              <CardAction>
                <Button size="sm">{t("settings.inviteMember")}</Button>
              </CardAction>
            </CardHeader>
            <CardContent className="p-0">
              {members.map((row, index) => (
                <div
                  key={row.member.id}
                  className={cn(
                    "flex items-center gap-3 px-5 py-3",
                    index > 0 && "border-t",
                  )}
                >
                  <UserAvatar user={row.member} size="sm" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {row.member.name}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {row.member.role}
                    </p>
                  </div>
                  <StatusBadge status={row.status} />
                  <Select defaultValue={row.access}>
                    <SelectTrigger size="sm" className="w-[110px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="Owner">
                        {t("settings.accessOwner")}
                      </SelectItem>
                      <SelectItem value="Admin">
                        {t("settings.accessAdmin")}
                      </SelectItem>
                      <SelectItem value="Member">
                        {t("settings.accessMember")}
                      </SelectItem>
                      <SelectItem value="Limited">
                        {t("settings.accessLimited")}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              ))}
            </CardContent>
          </Card>

          {/* ----------------------------------------------- integrations */}
          <Card>
            <CardHeader>
              <CardTitle>{t("settings.integrations")}</CardTitle>
              <CardDescription>
                {t("settings.integrationsDesc")}
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2">
              {integrations.map((integration) => (
                <div
                  key={integration.name}
                  className="flex items-start gap-3 rounded-lg border p-3.5"
                >
                  <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                    <Globe className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm leading-snug font-medium">
                      {integration.name}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {integration.detail}
                    </p>
                  </div>
                  <Button
                    variant={integration.connected ? "outline" : "default"}
                    size="sm"
                    className="shrink-0"
                  >
                    {integration.connected
                      ? t("buttons.manage")
                      : t("buttons.connect")}
                  </Button>
                </div>
              ))}
            </CardContent>
          </Card>

          {/* --------------------------------------------------- security */}
          {/* Live since Phase 3.7. The rows below it are still prototype
              affordances — password change and session management have
              backends but no UI yet. */}
          <MfaCard status={mfa} />

          <Card>
            <CardHeader>
              <CardTitle>{t("settings.security")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {[
                { icon: KeyRound, label: t("settings.passwordLabel"), detail: "Last changed 3 months ago", action: t("buttons.change") },
                { icon: Globe, label: t("settings.activeSessions"), detail: "3 devices signed in", action: t("buttons.review") },
              ].map((row, index) => (
                <div
                  key={row.label}
                  className={cn(
                    "flex items-center gap-3",
                    index > 0 && "border-t pt-4",
                  )}
                >
                  <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                    <row.icon className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{row.label}</p>
                    <p className="text-xs text-muted-foreground">{row.detail}</p>
                  </div>
                  <Button variant="outline" size="sm">
                    {row.action}
                  </Button>
                </div>
              ))}
            </CardContent>
          </Card>

          {/* ------------------------------------------------ danger zone */}
          <Card className="border-destructive/40">
            <CardHeader>
              <CardTitle className="text-destructive">
                {t("settings.dangerZone")}
              </CardTitle>
              <CardDescription>{t("settings.dangerZoneDesc")}</CardDescription>
            </CardHeader>
            <CardContent>
              <Button variant="destructive">
                <Trash2 className="size-4" />
                {t("settings.deleteWorkspace")}
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
