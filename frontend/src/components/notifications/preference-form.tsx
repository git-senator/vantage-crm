"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ClientApiError } from "@/lib/api/client";
import { useTranslation } from "@/i18n/language-provider";
import { saveNotificationPreferences } from "@/lib/api/notifications-client";
import type { NotificationPreference } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * Delivery preferences.
 *
 * The server materialises a row for every category — including the ones the
 * user has never touched — so this component holds no copy of the defaults.
 * Two definitions of "default" is one too many.
 *
 * Saves the whole set rather than one switch at a time, matching the API: a
 * per-switch PATCH would let two open tabs overwrite each other silently.
 */
export function NotificationPreferenceForm({
  preferences,
}: {
  preferences: NotificationPreference[];
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(preferences);
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = draft.some((row, index) => {
    const original = preferences[index];
    return row.in_app !== original.in_app || row.email !== original.email;
  });

  function toggle(
    category: string,
    channel: "in_app" | "email",
    value: boolean,
  ) {
    setSaved(false);
    setDraft((current) =>
      current.map((row) =>
        row.category === category ? { ...row, [channel]: value } : row,
      ),
    );
  }

  async function save() {
    setPending(true);
    setError(null);
    try {
      const updated = await saveNotificationPreferences(draft);
      setDraft(updated);
      setSaved(true);
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : t("body.notifSaveError"),
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end gap-6 text-[11px] text-muted-foreground">
        <span className="w-8 text-center">{t("body.notifInApp")}</span>
        <span className="w-8 text-center">{t("body.notifEmail")}</span>
      </div>

      {draft.map((row, index) => {
        const label = t(`body.notifCat_${row.category}`);
        return (
          <div
            key={row.category}
            className={cn("flex items-center gap-3 py-2", index > 0 && "border-t")}
          >
            <Label className="min-w-0 flex-1 text-sm font-normal">{label}</Label>
            <span className="flex w-8 justify-center">
              <Switch
                checked={row.in_app}
                onCheckedChange={(value) =>
                  toggle(row.category, "in_app", Boolean(value))
                }
                aria-label={`${t("body.notifInApp")}: ${label}`}
              />
            </span>
            <span className="flex w-8 justify-center">
              <Switch
                checked={row.email}
                onCheckedChange={(value) =>
                  toggle(row.category, "email", Boolean(value))
                }
                aria-label={`${t("body.notifEmail")}: ${label}`}
              />
            </span>
          </div>
        );
      })}

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      <div className="flex items-center justify-between gap-2 pt-1">
        <span className="text-[11px] text-muted-foreground">
          {saved && !dirty ? t("body.notifSavedMsg") : t("body.notifMutingHint")}
        </span>
        <Button size="sm" onClick={save} disabled={pending || !dirty}>
          {pending && <Loader2 className="size-4 animate-spin" />}
          {t("buttons.save")}
        </Button>
      </div>
    </div>
  );
}
