"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Loader2, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ClientApiError } from "@/lib/api/client";
import { useTranslation } from "@/i18n/language-provider";
import { createLead, updateLead } from "@/lib/api/leads-client";
import type { Lead, LeadInput } from "@/lib/api/types";

const STAGES = ["new", "contacted", "qualified", "touring", "unqualified"] as const;
const SOURCES = [
  "telegram",
  "youtube",
  "reddit",
  "zillow",
  "website",
  "referral",
  "open_house",
  "instagram",
  "cold_call",
  "realtor_com",
  "other",
] as const;
const TEMPERATURES = ["hot", "warm", "cold"] as const;

/**
 * Create and edit form.
 *
 * One component for both because the fields are identical — a separate edit
 * form is two places to add the next field to, and they drift.
 */
export function LeadForm({ lead }: { lead?: Lead }) {
  const router = useRouter();
  const { t } = useTranslation();
  const isEdit = lead !== undefined;

  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setFieldErrors({});
    setPending(true);

    const form = new FormData(event.currentTarget);
    const value = (key: string): string | null => {
      const raw = form.get(key);
      const text = typeof raw === "string" ? raw.trim() : "";
      return text === "" ? null : text;
    };

    const payload: LeadInput = {
      first_name: value("first_name") ?? "",
      last_name: value("last_name") ?? "",
      email: value("email"),
      phone: value("phone"),
      stage: (value("stage") ?? "new") as LeadInput["stage"],
      source: (value("source") ?? "other") as LeadInput["source"],
      temperature: (value("temperature") ?? "warm") as LeadInput["temperature"],
      budget_min: value("budget_min"),
      budget_max: value("budget_max"),
      preferred_location: value("preferred_location"),
      notes: value("notes"),
      tags: (value("tags") ?? "")
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
    };

    try {
      const saved = isEdit
        ? await updateLead(lead.id, payload)
        : await createLead(payload);

      router.push(`/leads/${saved.id}`);
      // Server components cache per request; without this the detail page
      // renders the pre-save data.
      router.refresh();
    } catch (caught) {
      if (caught instanceof ClientApiError) {
        setFieldErrors(caught.fieldErrors);
        setError(caught.message);
      } else {
        setError(t("body.formServerError"));
      }
      setPending(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6" noValidate>
      {error && (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/8 p-3 text-sm text-destructive"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          name="first_name"
          label={t("body.formFirstName")}
          required
          defaultValue={lead?.first_name}
          error={fieldErrors.first_name}
          disabled={pending}
        />
        <Field
          name="last_name"
          label={t("body.formLastName")}
          required
          defaultValue={lead?.last_name}
          error={fieldErrors.last_name}
          disabled={pending}
        />
        <Field
          name="email"
          label={t("body.formEmail")}
          type="email"
          defaultValue={lead?.email ?? ""}
          error={fieldErrors.email}
          disabled={pending}
        />
        <Field
          name="phone"
          label={t("body.formPhone")}
          defaultValue={lead?.phone ?? ""}
          error={fieldErrors.phone}
          disabled={pending}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Choice
          name="stage"
          label={t("body.formStage")}
          options={STAGES}
          defaultValue={lead?.stage ?? "new"}
          labelFor={(o) => t(`body.leadStage_${o}`)}
          disabled={pending}
        />
        <Choice
          name="source"
          label={t("body.formSource")}
          options={SOURCES}
          defaultValue={lead?.source ?? "other"}
          labelFor={(o) => t(`body.lfSource_${o}`)}
          disabled={pending}
        />
        <Choice
          name="temperature"
          label={t("body.formTemperature")}
          options={TEMPERATURES}
          defaultValue={lead?.temperature ?? "warm"}
          labelFor={(o) => t(`body.lfTemp_${o}`)}
          disabled={pending}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          name="budget_min"
          label={t("body.lfBudgetFrom")}
          type="number"
          placeholder="500000"
          defaultValue={lead?.budget_min ?? ""}
          error={fieldErrors.budget_min}
          disabled={pending}
        />
        <Field
          name="budget_max"
          label={t("body.lfBudgetTo")}
          type="number"
          placeholder="750000"
          defaultValue={lead?.budget_max ?? ""}
          error={fieldErrors.budget_max}
          disabled={pending}
        />
      </div>

      <Field
        name="preferred_location"
        label={t("body.lfPreferredLocation")}
        placeholder={t("body.lfLocationPlaceholder")}
        defaultValue={lead?.preferred_location ?? ""}
        error={fieldErrors.preferred_location}
        disabled={pending}
      />

      <Field
        name="tags"
        label={t("body.formTags")}
        placeholder={t("body.lfTagsPlaceholder")}
        hint={t("body.formTagsHint")}
        defaultValue={(lead?.tags ?? []).join(", ")}
        disabled={pending}
      />

      <div className="space-y-2">
        <Label htmlFor="notes">{t("body.formNotes")}</Label>
        <Textarea
          id="notes"
          name="notes"
          rows={4}
          placeholder={t("body.lfNotesPlaceholder")}
          defaultValue={lead?.notes ?? ""}
          disabled={pending}
        />
      </div>

      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="ghost"
          onClick={() => router.back()}
          disabled={pending}
        >
          {t("buttons.cancel")}
        </Button>
        <Button type="submit" disabled={pending}>
          {pending && <Loader2 className="size-4 animate-spin" />}
          {isEdit ? t("body.profSaveChanges") : t("body.lfCreate")}
        </Button>
      </div>
    </form>
  );
}

function Field({
  name,
  label: fieldLabel,
  hint,
  error,
  ...props
}: {
  name: string;
  label: string;
  hint?: string;
  error?: string;
} & React.ComponentProps<typeof Input>) {
  return (
    <div className="space-y-2">
      <Label htmlFor={name}>{fieldLabel}</Label>
      <Input id={name} name={name} aria-invalid={!!error} {...props} />
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

function Choice({
  name,
  label: fieldLabel,
  options,
  defaultValue,
  labelFor,
  disabled,
}: {
  name: string;
  label: string;
  options: readonly string[];
  defaultValue: string;
  labelFor: (option: string) => string;
  disabled?: boolean;
}) {
  // Controlled via a hidden input: Base UI's Select does not submit a native
  // form value, and FormData is what the handler reads.
  const [value, setValue] = useState(defaultValue);

  return (
    <div className="space-y-2">
      <Label htmlFor={name}>{fieldLabel}</Label>
      <input type="hidden" name={name} value={value} />
      <Select
        value={value}
        onValueChange={(next) => setValue(next ?? defaultValue)}
        disabled={disabled}
      >
        <SelectTrigger id={name} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {labelFor(option)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
