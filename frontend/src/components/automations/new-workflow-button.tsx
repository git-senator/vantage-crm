"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Loader2, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ClientApiError } from "@/lib/api/client";
import { useTranslation } from "@/i18n/language-provider";
import { createWorkflow } from "@/lib/api/automations-client";
import type { RegistryEntry } from "@/lib/api/types";

/**
 * Create a workflow: a name and a trigger, then straight into the builder.
 *
 * The trigger is chosen up front and not editable afterwards in this flow,
 * because it decides which fields conditions can test and which actions are
 * even offered — changing it mid-build would invalidate most of the graph.
 */
export function NewWorkflowButton({ triggers }: { triggers: RegistryEntry[] }) {
  const router = useRouter();
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [trigger, setTrigger] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return (
      <Button onClick={() => setOpen(true)}>
        <Plus className="size-4" />
        {t("body.autoNew")}
      </Button>
    );
  }

  const grouped = triggers.reduce<Record<string, RegistryEntry[]>>(
    (groups, entry) => {
      (groups[entry.category] ??= []).push(entry);
      return groups;
    },
    {},
  );

  return (
    <Card className="w-full max-w-md space-y-3 p-4">
      <div className="space-y-1">
        <Label htmlFor="wf-name" className="text-xs">
          {t("body.autoName")}
        </Label>
        <Input
          id="wf-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={t("body.autoNamePlaceholder")}
          disabled={pending}
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="wf-trigger" className="text-xs">
          {t("body.autoWhenHappens")}
        </Label>
        <select
          id="wf-trigger"
          className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
          value={trigger}
          disabled={pending}
          onChange={(event) => setTrigger(event.target.value)}
        >
          <option value="">{t("body.autoChooseTrigger")}</option>
          {Object.entries(grouped).map(([category, entries]) => (
            <optgroup key={category} label={category}>
              {entries.map((entry) => (
                <option key={entry.key} value={entry.key}>
                  {entry.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)} disabled={pending}>
          {t("buttons.cancel")}
        </Button>
        <Button
          size="sm"
          disabled={pending || !name.trim() || !trigger}
          onClick={async () => {
            setPending(true);
            setError(null);
            try {
              const created = await createWorkflow({
                name: name.trim(),
                trigger_type: trigger,
              });
              router.push(`/automations/${created.workflow.id}`);
            } catch (caught) {
              setError(
                caught instanceof ClientApiError
                  ? caught.message
                  : t("body.autoCreateError"),
              );
              setPending(false);
            }
          }}
        >
          {pending && <Loader2 className="size-4 animate-spin" />}
          {t("buttons.create")}
        </Button>
      </div>
    </Card>
  );
}
