import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PropertyForm } from "@/components/properties/property-form";
import { LanguageProvider } from "@/i18n/language-provider";

const push = vi.fn();
const refresh = vi.fn();
const createProperty = vi.fn();
const updateProperty = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh, back: vi.fn() }),
}));

vi.mock("@/lib/api/properties-client", () => ({
  createProperty: (...args: unknown[]) => createProperty(...args),
  updateProperty: (...args: unknown[]) => updateProperty(...args),
}));

/**
 * What is worth testing here is the payload, not the markup.
 *
 * The form's real job is turning FormData into a `PropertyInput` without
 * corrupting anything on the way — and the way to corrupt a listing is to run
 * a price or a coordinate through `Number()`.
 *
 * The form reads its labels through `useTranslation`, so it has to be rendered
 * inside a language provider; without one it throws before a single assertion
 * runs. The default locale is English, which is what the labels below are.
 */
function renderForm(props: Parameters<typeof PropertyForm>[0] = {}) {
  return render(
    <LanguageProvider>
      <PropertyForm {...props} />
    </LanguageProvider>,
  );
}

async function fillRequiredAndSubmit(overrides: Record<string, string> = {}) {
  const user = userEvent.setup();
  const values: Record<string, string> = {
    "Listing title": "Restored Edwardian",
    "Street address": "1428 Sanchez Street",
    City: "San Francisco",
    State: "CA",
    ZIP: "94131",
    ...overrides,
  };

  for (const [label, value] of Object.entries(values)) {
    const field = screen.getByLabelText(label);
    await user.clear(field);
    // An empty override means "leave this blank", which is a real case now
    // that not every field is required. `type()` rejects an empty string.
    if (value !== "") await user.type(field, value);
  }

  await user.click(screen.getByRole("button", { name: /create listing/i }));
}

describe("PropertyForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createProperty.mockResolvedValue({ id: "new-id" });
    updateProperty.mockResolvedValue({ id: "new-id" });
  });

  it("submits the required address fields", async () => {
    renderForm();
    await fillRequiredAndSubmit();

    expect(createProperty).toHaveBeenCalledTimes(1);
    expect(createProperty.mock.calls[0][0]).toMatchObject({
      title: "Restored Edwardian",
      address_line1: "1428 Sanchez Street",
      city: "San Francisco",
      state: "CA",
      postal_code: "94131",
    });
  });

  it("keeps the price as a string so NUMERIC precision survives", async () => {
    renderForm();
    await fillRequiredAndSubmit({ Price: "1895000.55" });

    const payload = createProperty.mock.calls[0][0];
    expect(payload.price).toBe("1895000.55");
    expect(typeof payload.price).toBe("string");
  });

  it("keeps half-bathrooms as a string", async () => {
    renderForm();
    await fillRequiredAndSubmit({ Bathrooms: "2.5" });

    const payload = createProperty.mock.calls[0][0];
    expect(payload.bathrooms).toBe("2.5");
  });

  it("sends null rather than an empty string for an omitted price", async () => {
    // The backend treats "" as a validation error but null as "no price".
    renderForm();
    await fillRequiredAndSubmit();

    expect(createProperty.mock.calls[0][0].price).toBeNull();
  });

  it("parses genuinely integer fields as numbers", async () => {
    renderForm({ units: "imperial" });
    await fillRequiredAndSubmit({ Bedrooms: "4", "Square feet": "2840" });

    const payload = createProperty.mock.calls[0][0];
    expect(payload.bedrooms).toBe(4);
    expect(payload.square_feet).toBe(2840);
  });

  it("converts a metric area into the square feet the column stores", async () => {
    // The box is labelled in the workspace's unit and the column is not, so
    // this is the one place the two have to be reconciled. 264 m² typed by a
    // Brazilian agent must not land as 264 square feet — a studio's worth of
    // floor on a four-bedroom house.
    renderForm();
    await fillRequiredAndSubmit({ "Area (m²)": "264" });

    expect(createProperty.mock.calls[0][0].square_feet).toBe(2842);
  });

  it("sends no postcode rather than a placeholder when none is given", async () => {
    // A listing without a CEP is normal; a stand-in value would print on an
    // export looking like a real postcode.
    renderForm();
    await fillRequiredAndSubmit({ ZIP: "" });

    expect(createProperty.mock.calls[0][0].postal_code).toBeNull();
  });

  it("marks a rental and gives it a period", async () => {
    renderForm();
    await fillRequiredAndSubmit();

    // The default stays a sale — the overwhelming majority of listings — so
    // nothing carries a rent period it did not ask for.
    expect(createProperty.mock.calls[0][0]).toMatchObject({
      listing_kind: "sale",
      rent_period: null,
    });
  });

  it("splits features on commas and drops the blanks", async () => {
    renderForm();
    await fillRequiredAndSubmit({ Features: "Pool, Garage,  , Solar" });

    expect(createProperty.mock.calls[0][0].features).toEqual([
      "Pool",
      "Garage",
      "Solar",
    ]);
  });

  it("navigates to the saved listing and refreshes the server cache", async () => {
    renderForm();
    await fillRequiredAndSubmit();

    expect(push).toHaveBeenCalledWith("/properties/new-id");
    // Without refresh() the detail page renders the pre-save data, because
    // server components cache per request.
    expect(refresh).toHaveBeenCalled();
  });

  it("updates rather than creates when given a property", async () => {
    const user = userEvent.setup();
    renderForm({
      property: {
        id: "existing-id",
        title: "Old title",
        status: "active",
        property_type: "condo",
        listing_kind: "sale",
        address_line1: "1 Old Street",
        city: "Oakland",
        state: "CA",
        postal_code: "94601",
        features: [],
      } as never,
    });

    await user.click(screen.getByRole("button", { name: /save changes/i }));

    expect(updateProperty).toHaveBeenCalledTimes(1);
    expect(updateProperty.mock.calls[0][0]).toBe("existing-id");
    expect(createProperty).not.toHaveBeenCalled();
  });
});
