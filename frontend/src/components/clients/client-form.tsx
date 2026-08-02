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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ClientApiError } from "@/lib/api/client";
import { useTranslation } from "@/i18n/language-provider";
import { createClient, updateClient } from "@/lib/api/clients-client";
import type { Client, ClientInput } from "@/lib/api/types";

const TYPES = [
  "buyer",
  "seller",
  "investor",
  "landlord",
  "tenant",
  "other",
] as const;
const STATUSES = ["active", "under_contract", "dormant", "past"] as const;

/**
 * Create and edit form.
 *
 * One component for both because the fields are identical — a separate edit
 * form is two places to add the next field to, and they drift.
 *
 * The person/company toggle mirrors `ck_clients_identity`: a client is either
 * a named person or a named company. Submitting only the fields for the
 * selected kind keeps the payload consistent with that rule, and the server
 * enforces it regardless of what this form sends.
 */
export function ClientForm({ client }: { client?: Client }) {
  const router = useRouter();
  const { t } = useTranslation();
  const isEdit = client !== undefined;

  const [kind, setKind] = useState<"person" | "company">(
    client?.is_company ? "company" : "person",
  );
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

    const isCompany = kind === "company";
    const payload: ClientInput = {
      // Null out the identity fields the selected kind does not use, rather
      // than leaving stale values behind on an edit that switched kind.
      first_name: isCompany ? null : value("first_name"),
      last_name: isCompany ? null : value("last_name"),
      company_name: isCompany ? value("company_name") : null,
      email: value("email"),
      phone: value("phone"),
      type: (value("type") ?? "buyer") as ClientInput["type"],
      status: (value("status") ?? "active") as ClientInput["status"],
      lifetime_value: value("lifetime_value"),
      client_since: value("client_since"),
      notes: value("notes"),
      tags: (value("tags") ?? "")
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
    };

    try {
      const saved = isEdit
        ? await updateClient(client.id, payload)
        : await createClient(payload);

      router.push(`/clients/${saved.id}`);
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

      <div className="space-y-2">
        <Label>{t("body.cfIntro")}</Label>
        <Tabs
          value={kind}
          onValueChange={(next) => setKind(next as "person" | "company")}
        >
          <TabsList>
            <TabsTrigger value="person" disabled={pending}>
              {t("body.cfPerson")}
            </TabsTrigger>
            <TabsTrigger value="company" disabled={pending}>
              {t("body.cfCompany")}
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {kind === "person" ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            name="first_name"
            label={t("body.formFirstName")}
            required
            defaultValue={client?.first_name ?? ""}
            error={fieldErrors.first_name}
            disabled={pending}
          />
          <Field
            name="last_name"
            label={t("body.formLastName")}
            required
            defaultValue={client?.last_name ?? ""}
            error={fieldErrors.last_name}
            disabled={pending}
          />
        </div>
      ) : (
        <Field
          name="company_name"
          label={t("body.cfCompanyName")}
          required
          placeholder="Tanaka Holdings Co"
          defaultValue={client?.company_name ?? ""}
          error={fieldErrors.company_name}
          disabled={pending}
        />
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          name="email"
          label={t("body.formEmail")}
          type="email"
          defaultValue={client?.email ?? ""}
          error={fieldErrors.email}
          disabled={pending}
        />
        <Field
          name="phone"
          label={t("body.formPhone")}
          defaultValue={client?.phone ?? ""}
          error={fieldErrors.phone}
          disabled={pending}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Choice
          name="type"
          label={t("body.formType")}
          options={TYPES}
          defaultValue={client?.type ?? "buyer"}
          labelFor={(o) => t(`body.clientType_${o}`)}
          disabled={pending}
        />
        <Choice
          name="status"
          label={t("body.formStatus")}
          options={STATUSES}
          defaultValue={client?.status ?? "active"}
          labelFor={(o) => t(`body.clientStatus_${o}`)}
          disabled={pending}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          name="lifetime_value"
          label={t("body.cfLifetimeValue")}
          type="number"
          placeholder="2450000"
          defaultValue={client?.lifetime_value ?? ""}
          error={fieldErrors.lifetime_value}
          disabled={pending}
        />
        <Field
          name="client_since"
          label={t("body.cfClientSince")}
          type="date"
          defaultValue={client?.client_since ?? ""}
          error={fieldErrors.client_since}
          disabled={pending}
        />
      </div>

      <Field
        name="tags"
        label={t("body.formTags")}
        placeholder="VIP, Repeat"
        hint={t("body.formTagsHint")}
        defaultValue={(client?.tags ?? []).join(", ")}
        disabled={pending}
      />

      <div className="space-y-2">
        <Label htmlFor="notes">{t("body.formNotes")}</Label>
        <Textarea
          id="notes"
          name="notes"
          rows={4}
          placeholder={t("body.cfNotesPlaceholder")}
          defaultValue={client?.notes ?? ""}
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
          {isEdit ? t("body.profSaveChanges") : t("body.cfCreate")}
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
