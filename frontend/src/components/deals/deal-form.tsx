"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";
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
import { ClientApiError } from "@/lib/api/client";
import { percentToRate, rateToPercent } from "@/lib/commission";
import { useTranslation } from "@/i18n/language-provider";
import { createDeal, updateDeal } from "@/lib/api/deals-client";
import type { Client, Deal, DealInput, Property } from "@/lib/api/types";

const PRIORITIES = ["low", "medium", "high", "urgent"] as const;

/**
 * Create and edit form.
 *
 * Note what is absent: **no stage selector.** Moving a deal between stages is a
 * domain action that writes history and resets probability, so it lives on the
 * detail page and the board, not here. A stage dropdown on an edit form would
 * imply it is just another field.
 */
export function DealForm({
  deal,
  clients,
  properties,
}: {
  deal?: Deal;
  clients: Pick<Client, "id" | "display_name">[];
  properties: Pick<Property, "id" | "title">[];
}) {
  const router = useRouter();
  const { t, locale } = useTranslation();
  const isEdit = deal !== undefined;

  const [clientId, setClientId] = useState(deal?.client.id ?? clients[0]?.id ?? "");
  const [propertyId, setPropertyId] = useState(deal?.listing?.id ?? "none");
  const [priority, setPriority] = useState(deal?.priority ?? "medium");
  // The three money fields are controlled so the commission can be shown while
  // it is being typed. Before this, the arithmetic happened on the server and
  // you only learned the figure after the deal was saved.
  const [dealValue, setDealValue] = useState(String(deal?.value ?? ""));
  // Percent on screen; converted back to the 0…1 fraction on submit.
  const [ratePercent, setRatePercent] = useState(
    rateToPercent(deal?.commission_rate),
  );
  const [amount, setAmount] = useState(String(deal?.commission_amount ?? ""));
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);

  /**
   * One line under the money row, mirroring the rule the server applies: a
   * typed amount wins, otherwise the rate decides. Typed an amount instead?
   * Then the useful number is the percentage it works out to — which is the
   * question a rental raises, where the fee is a month's rent and nobody
   * knows offhand what share of the year that is.
   */
  const commissionHint = useMemo(() => {
    const v = Number(dealValue);
    const a = Number(amount);
    const r = Number(ratePercent) / 100;
    if (!Number.isFinite(v) || v <= 0) return null;
    if (amount.trim() !== "" && Number.isFinite(a) && a > 0) {
      const percent = new Intl.NumberFormat(locale, {
        style: "percent",
        maximumFractionDigits: 2,
      }).format(a / v);
      return t("body.dfCommissionImplied", { percent });
    }
    if (ratePercent.trim() !== "" && Number.isFinite(r) && r > 0) {
      return t("body.dfCommissionPreview", {
        // Grouped in the reader's locale, like the percentage above it —
        // en-US separators next to a Russian percent sign read as a bug.
        amount: new Intl.NumberFormat(locale).format(Math.round(v * r)),
      });
    }
    return null;
  }, [dealValue, amount, ratePercent, locale, t]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setFieldErrors({});

    if (!clientId) {
      setError(t("body.dfNeedClient"));
      return;
    }
    setPending(true);

    const form = new FormData(event.currentTarget);
    const value = (key: string): string | null => {
      const raw = form.get(key);
      const text = typeof raw === "string" ? raw.trim() : "";
      return text === "" ? null : text;
    };

    // Money stays a string end to end. Parsing a value or a commission rate
    // into a JS number here would round somebody's cheque.
    const payload: DealInput = {
      title: value("title") ?? "",
      client_id: clientId,
      property_id: propertyId === "none" ? null : propertyId,
      value: value("value"),
      commission_amount: value("commission_amount"),
      // The field asks for a percent because that is how a rate is agreed;
      // the column wants the fraction.
      commission_rate: percentToRate(form.get("commission_rate") as string ?? ""),
      priority: priority as DealInput["priority"],
      expected_close_date: value("expected_close_date"),
    };

    try {
      const saved = isEdit
        ? await updateDeal(deal.id, payload)
        : await createDeal(payload);

      router.push(`/deals/${saved.id}`);
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

      <Field
        name="title"
        label={t("body.dfTitle")}
        required
        placeholder="1428 Sanchez — Lindqvist purchase"
        defaultValue={deal?.title}
        error={fieldErrors.title}
        disabled={pending}
      />

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="client_id">{t("body.dfClient")}</Label>
          <Select value={clientId} onValueChange={(v) => setClientId(v ?? "")} disabled={pending}>
            <SelectTrigger id="client_id" className="w-full">
              <SelectValue placeholder={t("body.dfSelectClient")} />
            </SelectTrigger>
            <SelectContent>
              {clients.map((option) => (
                <SelectItem key={option.id} value={option.id}>
                  {option.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {clients.length === 0 && (
            <p className="text-xs text-destructive">{t("body.dfNoClients")}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="property_id">{t("body.dfProperty")}</Label>
          <Select
            value={propertyId}
            onValueChange={(v) => setPropertyId(v ?? "none")}
            disabled={pending}
          >
            <SelectTrigger id="property_id" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {/* Optional: a buyer-representation deal has no listing until an
                  offer is made. */}
              <SelectItem value="none">{t("body.dfNoProperty")}</SelectItem>
              {properties.map((option) => (
                <SelectItem key={option.id} value={option.id}>
                  {option.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Field
          name="value"
          label={t("body.dfValue")}
          type="number"
          placeholder="1895000"
          value={dealValue}
          onChange={(e) => setDealValue(e.target.value)}
          error={fieldErrors.value}
          disabled={pending}
        />
        <Field
          name="commission_rate"
          label={t("body.dfCommissionRate")}
          type="number"
          step="0.01"
          min="0"
          max="100"
          placeholder="5"
          value={ratePercent}
          onChange={(e) => setRatePercent(e.target.value)}
          error={fieldErrors.commission_rate}
          disabled={pending}
        />
        <Field
          name="commission_amount"
          label={t("body.dfCommissionAmount")}
          type="number"
          placeholder={t("body.dfAmountPlaceholder")}
          hint={t("body.dfAmountHint")}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          error={fieldErrors.commission_amount}
          disabled={pending}
        />
      </div>

      {commissionHint && (
        <p
          aria-live="polite"
          className="-mt-2 rounded-md bg-muted px-3 py-2 text-sm text-muted-foreground"
        >
          {commissionHint}
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="priority">{t("body.dfPriority")}</Label>
          <Select
            value={priority}
            onValueChange={(v) => setPriority((v ?? "medium") as typeof priority)}
            disabled={pending}
          >
            <SelectTrigger id="priority" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PRIORITIES.map((option) => (
                <SelectItem key={option} value={option}>
                  {t(`body.dfPriority_${option}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Field
          name="expected_close_date"
          label={t("body.dfExpectedClose")}
          type="date"
          defaultValue={deal?.expected_close_date ?? ""}
          error={fieldErrors.expected_close_date}
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
        <Button type="submit" disabled={pending || clients.length === 0}>
          {pending && <Loader2 className="size-4 animate-spin" />}
          {isEdit ? t("body.profSaveChanges") : t("body.dfCreate")}
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
