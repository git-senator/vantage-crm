"use client";

import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
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
import { useTranslation } from "@/i18n/language-provider";
import { updateOrganization } from "@/lib/api/organization-client";
import type { Organization, WorkspaceSettings } from "@/lib/api/types";

const TIMEZONES: { value: string; labelKey: string }[] = [
  { value: "America/Sao_Paulo", labelKey: "body.tzSaoPaulo" },
  { value: "Pacific Time (US & Canada)", labelKey: "body.tzPacific" },
  { value: "Mountain Time", labelKey: "body.tzMountain" },
  { value: "Central Time", labelKey: "body.tzCentral" },
  { value: "Eastern Time", labelKey: "body.tzEastern" },
];

const CURRENCIES = ["BRL (R$)", "USD ($)", "EUR (€)"];

const TONES: { value: string; labelKey: string }[] = [
  { value: "Professional", labelKey: "settings.toneProfessional" },
  { value: "Warm and conversational", labelKey: "settings.toneWarm" },
  { value: "Concise", labelKey: "settings.toneConcise" },
  { value: "Formal", labelKey: "settings.toneFormal" },
];

/**
 * Workspace + AI settings, persisted.
 *
 * Loads the organization's name and its `settings` JSONB and writes both back
 * through `PATCH /organizations/current`. Every field has a default so a fresh
 * workspace (empty settings) renders sensibly; only what the person changes is
 * meaningful, and Save sends the whole resolved object so a partial write never
 * drops a key the server already had.
 */
export function WorkspaceSettingsForm({
  organization,
}: {
  organization: Organization;
}) {
  const { t } = useTranslation();
  const s = organization.settings ?? {};

  const [name, setName] = useState(organization.name);
  const [license, setLicense] = useState(s.license ?? "");
  const [timezone, setTimezone] = useState(s.timezone ?? "America/Sao_Paulo");
  const [currency, setCurrency] = useState(s.currency ?? "BRL (R$)");
  const [weekend, setWeekend] = useState(s.weekend_notifications ?? false);
  const [autoAssign, setAutoAssign] = useState(s.auto_assign ?? true);
  const [dealApproval, setDealApproval] = useState(s.deal_approval ?? true);
  const [threshold, setThreshold] = useState(s.ai_lead_score_threshold ?? 80);
  const [tone, setTone] = useState(s.ai_drafting_tone ?? "Professional");
  const [suggest, setSuggest] = useState(s.ai_suggest_replies ?? true);
  const [briefing, setBriefing] = useState(s.ai_daily_briefing ?? true);
  const [summarize, setSummarize] = useState(s.ai_auto_summarize ?? false);
  const [pending, setPending] = useState(false);

  async function save() {
    setPending(true);
    const settings: WorkspaceSettings = {
      license,
      timezone,
      currency,
      weekend_notifications: weekend,
      auto_assign: autoAssign,
      deal_approval: dealApproval,
      ai_lead_score_threshold: threshold,
      ai_drafting_tone: tone,
      ai_suggest_replies: suggest,
      ai_daily_briefing: briefing,
      ai_auto_summarize: summarize,
    };
    try {
      await updateOrganization({ name: name.trim() || organization.name, settings });
      toast.success(t("settings.settingsSaved"));
    } catch {
      toast.error(t("body.errGeneric"));
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      {/* ---------------------------------------------------------- general */}
      <Card id="general" className="scroll-mt-20">
        <CardHeader>
          <CardTitle>{t("settings.workspaceDetails")}</CardTitle>
          <CardDescription>{t("settings.workspaceDetailsDesc")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="org">{t("settings.brokerageName")}</Label>
              <Input
                id="org"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="license">{t("settings.brokerageLicense")}</Label>
              <Input
                id="license"
                value={license}
                onChange={(e) => setLicense(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="timezone">{t("settings.timezone")}</Label>
              <Select value={timezone} onValueChange={(v) => setTimezone(v ?? timezone)}>
                <SelectTrigger id="timezone">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TIMEZONES.map((tz) => (
                    <SelectItem key={tz.value} value={tz.value}>
                      {t(tz.labelKey)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="currency">{t("settings.currency")}</Label>
              <Select value={currency} onValueChange={(v) => setCurrency(v ?? currency)}>
                <SelectTrigger id="currency">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CURRENCIES.map((c) => (
                    <SelectItem key={c} value={c}>
                      {c}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <Separator />

          <div className="space-y-4">
            <ToggleRow
              label={t("settings.weekendNotifications")}
              detail={t("settings.weekendNotificationsDetail")}
              checked={weekend}
              onChange={setWeekend}
            />
            <ToggleRow
              label={t("settings.autoAssign")}
              detail={t("settings.autoAssignDetail")}
              checked={autoAssign}
              onChange={setAutoAssign}
            />
            <ToggleRow
              label={t("settings.dealApproval")}
              detail={t("settings.dealApprovalDetail")}
              checked={dealApproval}
              onChange={setDealApproval}
            />
          </div>
        </CardContent>
      </Card>

      {/* --------------------------------------------------------------- AI */}
      <Card id="ai" className="scroll-mt-20">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="size-4 text-primary" />
            {t("settings.aiPreferences")}
          </CardTitle>
          <CardDescription>{t("settings.aiPreferencesDesc")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="space-y-3">
            <div className="flex items-baseline justify-between">
              <Label>{t("settings.leadScoreThreshold")}</Label>
              <span className="tabular text-sm font-medium">{threshold}</span>
            </div>
            <Slider
              value={[threshold]}
              max={100}
              step={5}
              onValueChange={(v) =>
                setThreshold((Array.isArray(v) ? v[0] : v) ?? threshold)
              }
            />
            <p className="text-sm text-muted-foreground">
              {t("settings.leadScoreThresholdHint")}
            </p>
          </div>

          <Separator />

          <div className="space-y-2">
            <Label htmlFor="tone">{t("settings.draftingTone")}</Label>
            <Select value={tone} onValueChange={(v) => setTone(v ?? tone)}>
              <SelectTrigger id="tone" className="sm:w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TONES.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {t(option.labelKey)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-4">
            <ToggleRow
              label={t("settings.suggestReplies")}
              detail={t("settings.suggestRepliesDetail")}
              checked={suggest}
              onChange={setSuggest}
            />
            <ToggleRow
              label={t("settings.dailyBriefing")}
              detail={t("settings.dailyBriefingDetail")}
              checked={briefing}
              onChange={setBriefing}
            />
            <ToggleRow
              label={t("settings.autoSummarize")}
              detail={t("settings.autoSummarizeDetail")}
              checked={summarize}
              onChange={setSummarize}
            />
          </div>

          <div className="flex justify-end pt-2">
            <Button onClick={save} disabled={pending}>
              {pending && <Loader2 className="size-4 animate-spin" />}
              {t("buttons.saveChanges")}
            </Button>
          </div>
        </CardContent>
      </Card>
    </>
  );
}

function ToggleRow({
  label,
  detail,
  checked,
  onChange,
}: {
  label: string;
  detail: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <Label className="text-sm">{label}</Label>
        <p className="mt-0.5 text-sm text-muted-foreground">{detail}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onChange} aria-label={label} />
    </div>
  );
}
