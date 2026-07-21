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

/** `under_contract` -> `Under contract`. Keeps the wire format snake_case. */
function label(value: string): string {
  const spaced = value.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

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
        setError("Unable to reach the server. Please try again.");
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
        <Label>This client is</Label>
        <Tabs
          value={kind}
          onValueChange={(next) => setKind(next as "person" | "company")}
        >
          <TabsList>
            <TabsTrigger value="person" disabled={pending}>
              A person
            </TabsTrigger>
            <TabsTrigger value="company" disabled={pending}>
              A company
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {kind === "person" ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            name="first_name"
            label="First name"
            required
            defaultValue={client?.first_name ?? ""}
            error={fieldErrors.first_name}
            disabled={pending}
          />
          <Field
            name="last_name"
            label="Last name"
            required
            defaultValue={client?.last_name ?? ""}
            error={fieldErrors.last_name}
            disabled={pending}
          />
        </div>
      ) : (
        <Field
          name="company_name"
          label="Company name"
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
          label="Email"
          type="email"
          defaultValue={client?.email ?? ""}
          error={fieldErrors.email}
          disabled={pending}
        />
        <Field
          name="phone"
          label="Phone"
          defaultValue={client?.phone ?? ""}
          error={fieldErrors.phone}
          disabled={pending}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Choice
          name="type"
          label="Type"
          options={TYPES}
          defaultValue={client?.type ?? "buyer"}
          disabled={pending}
        />
        <Choice
          name="status"
          label="Status"
          options={STATUSES}
          defaultValue={client?.status ?? "active"}
          disabled={pending}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          name="lifetime_value"
          label="Lifetime value"
          type="number"
          placeholder="2450000"
          defaultValue={client?.lifetime_value ?? ""}
          error={fieldErrors.lifetime_value}
          disabled={pending}
        />
        <Field
          name="client_since"
          label="Client since"
          type="date"
          defaultValue={client?.client_since ?? ""}
          error={fieldErrors.client_since}
          disabled={pending}
        />
      </div>

      <Field
        name="tags"
        label="Tags"
        placeholder="VIP, Repeat"
        hint="Comma separated"
        defaultValue={(client?.tags ?? []).join(", ")}
        disabled={pending}
      />

      <div className="space-y-2">
        <Label htmlFor="notes">Notes</Label>
        <Textarea
          id="notes"
          name="notes"
          rows={4}
          placeholder="What matters about this relationship…"
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
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending && <Loader2 className="size-4 animate-spin" />}
          {isEdit ? "Save changes" : "Create client"}
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
  disabled,
}: {
  name: string;
  label: string;
  options: readonly string[];
  defaultValue: string;
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
              {label(option)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
