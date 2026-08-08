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
import { createProperty, updateProperty } from "@/lib/api/properties-client";
import type { ListingKind, Property, PropertyInput } from "@/lib/api/types";
import {
  areaFromSquareFeet,
  areaToSquareFeet,
  type MeasurementSystem,
} from "@/lib/format";

const STATUSES = [
  "active",
  "pending",
  "sold",
  "off_market",
  "coming_soon",
] as const;
const TYPES = [
  "single_family",
  "condo",
  "townhouse",
  "multi_family",
  "land",
  "commercial",
] as const;
const LISTING_KINDS = ["sale", "rent"] as const;
const RENT_PERIODS = ["month", "week", "day"] as const;

/**
 * Create and edit form.
 *
 * One component for both because the fields are identical — a separate edit
 * form is two places to add the next field to, and they drift.
 */
export function PropertyForm({
  property,
  units = "metric",
}: {
  property?: Property;
  /** The workspace's unit system, from the session on the page above. */
  units?: MeasurementSystem;
}) {
  const router = useRouter();
  const { t } = useTranslation();
  const isEdit = property !== undefined;

  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  // Held in state because the period field only makes sense on a rental, and
  // a sale that carries one is rejected by the API.
  const [kind, setKind] = useState<ListingKind>(
    property?.listing_kind ?? "sale",
  );

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
    const asNumber = (key: string): number | null => {
      const raw = value(key);
      if (raw === null) return null;
      const parsed = Number(raw);
      return Number.isFinite(parsed) ? parsed : null;
    };
    const withUnits = (area: number | null): number | null =>
      area === null ? null : areaToSquareFeet(area, units);

    const payload: PropertyInput = {
      title: value("title") ?? "",
      mls_number: value("mls_number"),
      status: (value("status") ?? "active") as PropertyInput["status"],
      property_type: (value("property_type") ??
        "single_family") as PropertyInput["property_type"],
      listing_kind: kind,
      // A sale must send no period at all; a rental sends what was chosen and
      // the API fills in "month" if that is somehow blank.
      rent_period:
        kind === "rent"
          ? ((value("rent_period") ?? "month") as PropertyInput["rent_period"])
          : null,
      address_line1: value("address_line1") ?? "",
      address_line2: value("address_line2"),
      city: value("city") ?? "",
      state: value("state") ?? "",
      postal_code: value("postal_code"),
      // Money and coordinates stay strings end to end: parsing them into a
      // JS number here would round a price and silently move a pin.
      price: value("price"),
      bedrooms: asNumber("bedrooms"),
      bathrooms: value("bathrooms"),
      // The box is labelled in the workspace's unit; the column stores square
      // feet, so what a person typed is converted here rather than anywhere
      // that could forget to.
      square_feet: withUnits(asNumber("square_feet")),
      lot_size_sqft: withUnits(asNumber("lot_size_sqft")),
      year_built: asNumber("year_built"),
      listed_at: value("listed_at"),
      description: value("description"),
      features: (value("features") ?? "")
        .split(",")
        .map((feature) => feature.trim())
        .filter(Boolean),
    };

    try {
      const saved = isEdit
        ? await updateProperty(property.id, payload)
        : await createProperty(payload);

      router.push(`/properties/${saved.id}`);
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
        label={t("body.pfTitle")}
        required
        placeholder={t("body.pfTitlePlaceholder")}
        defaultValue={property?.title}
        error={fieldErrors.title}
        disabled={pending}
      />

      <div className="grid gap-4 sm:grid-cols-3">
        <Choice
          name="status"
          label={t("body.formStatus")}
          options={STATUSES}
          defaultValue={property?.status ?? "active"}
          labelFor={(o) => t(`body.propStatus_${o}`)}
          disabled={pending}
        />
        <Choice
          name="property_type"
          label={t("body.formType")}
          options={TYPES}
          defaultValue={property?.property_type ?? "single_family"}
          labelFor={(o) => t(`body.propType_${o}`)}
          disabled={pending}
        />
        <Field
          name="mls_number"
          label={t("body.pfMls")}
          placeholder="MLS-4471"
          defaultValue={property?.mls_number ?? ""}
          error={fieldErrors.mls_number}
          disabled={pending}
        />
      </div>

      <div className="space-y-4 rounded-lg border p-4">
        <p className="text-sm font-medium">{t("body.pfAddress")}</p>
        <Field
          name="address_line1"
          label={t("body.pfStreet")}
          required
          placeholder="1428 Sanchez Street"
          defaultValue={property?.address_line1}
          error={fieldErrors.address_line1}
          disabled={pending}
        />
        <Field
          name="address_line2"
          label={t("body.pfUnit")}
          defaultValue={property?.address_line2 ?? ""}
          error={fieldErrors.address_line2}
          disabled={pending}
        />
        <div className="grid gap-4 sm:grid-cols-3">
          <Field
            name="city"
            label={t("body.pfCity")}
            required
            defaultValue={property?.city}
            error={fieldErrors.city}
            disabled={pending}
          />
          <Field
            name="state"
            label={t("body.pfState")}
            required
            placeholder="CA"
            defaultValue={property?.state}
            error={fieldErrors.state}
            disabled={pending}
          />
          {/* Not required: a listing with no postcode is a normal listing, and
              insisting on one is what puts a placeholder onto a contract. */}
          <Field
            name="postal_code"
            label={t("body.pfZip")}
            placeholder="88061-000"
            defaultValue={property?.postal_code ?? ""}
            error={fieldErrors.postal_code}
            disabled={pending}
          />
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Choice
          name="listing_kind"
          label={t("forms.listingKind")}
          options={LISTING_KINDS}
          defaultValue={kind}
          onValueChange={(next) => setKind(next as ListingKind)}
          labelFor={(o) => t(o === "rent" ? "body.dealRent" : "body.dealSale")}
          disabled={pending}
        />
        {kind === "rent" && (
          <Choice
            name="rent_period"
            label={t("forms.rentPeriod")}
            options={RENT_PERIODS}
            defaultValue={property?.rent_period ?? "month"}
            labelFor={(o) => t(`body.rentPeriod_${o}`)}
            disabled={pending}
          />
        )}
        <Field
          name="price"
          label={t(kind === "rent" ? "body.pfRent" : "body.pfPrice")}
          type="number"
          placeholder="1895000"
          hint={t("body.pfPriceHint")}
          defaultValue={property?.price ?? ""}
          error={fieldErrors.price}
          disabled={pending}
        />
        <Field
          name="listed_at"
          label={t("body.pfListedOn")}
          type="date"
          defaultValue={property?.listed_at ?? ""}
          error={fieldErrors.listed_at}
          disabled={pending}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Field
          name="bedrooms"
          label={t("body.pfBedrooms")}
          type="number"
          defaultValue={property?.bedrooms ?? ""}
          error={fieldErrors.bedrooms}
          disabled={pending}
        />
        <Field
          name="bathrooms"
          label={t("body.pfBathrooms")}
          type="number"
          step="0.5"
          hint={t("body.pfBathsHint")}
          defaultValue={property?.bathrooms ?? ""}
          error={fieldErrors.bathrooms}
          disabled={pending}
        />
        <Field
          name="square_feet"
          label={t(units === "imperial" ? "body.pfSquareFeet" : "body.pfAreaM2")}
          type="number"
          defaultValue={
            property?.square_feet != null
              ? areaFromSquareFeet(property.square_feet, units)
              : ""
          }
          error={fieldErrors.square_feet}
          disabled={pending}
        />
        <Field
          name="year_built"
          label={t("body.pfYearBuilt")}
          type="number"
          defaultValue={property?.year_built ?? ""}
          error={fieldErrors.year_built}
          disabled={pending}
        />
      </div>

      <Field
        name="features"
        label={t("body.pfFeatures")}
        placeholder={t("body.pfFeaturesPlaceholder")}
        hint={t("body.formTagsHint")}
        defaultValue={(property?.features ?? []).join(", ")}
        disabled={pending}
      />

      <div className="space-y-2">
        <Label htmlFor="description">{t("body.pfDescription")}</Label>
        <Textarea
          id="description"
          name="description"
          rows={5}
          placeholder={t("body.pfDescPlaceholder")}
          defaultValue={property?.description ?? ""}
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
          {isEdit ? t("body.profSaveChanges") : t("body.pfCreate")}
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
  onValueChange,
}: {
  name: string;
  label: string;
  options: readonly string[];
  defaultValue: string;
  labelFor: (option: string) => string;
  disabled?: boolean;
  /** For a choice the rest of the form has to react to, such as sale vs rent. */
  onValueChange?: (value: string) => void;
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
        onValueChange={(next) => {
          const resolved = next ?? defaultValue;
          setValue(resolved);
          onValueChange?.(resolved);
        }}
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
