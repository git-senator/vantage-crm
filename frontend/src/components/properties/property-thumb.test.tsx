import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PropertyThumb, hueFor } from "@/components/properties/property-thumb";
import type { Property } from "@/lib/api/types";

function makeProperty(overrides: Partial<Property> = {}): Property {
  return {
    id: "019f8584-c0d2-7eae-885a-86ed8e75d3ea",
    title: "Restored Edwardian with garden",
    mls_number: null,
    status: "active",
    property_type: "single_family",
    listing_kind: "sale",
    rent_period: null,
    address_line1: "1428 Sanchez Street",
    address_line2: null,
    city: "San Francisco",
    state: "CA",
    postal_code: "94131",
    country: "US",
    full_address: "1428 Sanchez Street, San Francisco, CA 94131",
    latitude: null,
    longitude: null,
    price: "1895000.00",
    currency: "USD",
    bedrooms: 4,
    bathrooms: "2.5",
    square_feet: 2840,
    lot_size_sqft: null,
    year_built: null,
    listed_at: null,
    days_on_market: null,
    view_count: 0,
    save_count: 0,
    description: null,
    features: [],
    custom_fields: {},
    client_id: null,
    listing_agent: null,
    cover_attachment_id: null,
    cover_url: null,
    created_at: "2026-07-21T10:00:00Z",
    updated_at: "2026-07-21T10:00:00Z",
    ...overrides,
  };
}

describe("hueFor", () => {
  it("is deterministic for a given id", () => {
    // The prototype keyed the gradient to list index, so a card changed colour
    // whenever the list was filtered or paged. Keying it to the id is what
    // makes a listing recognisable.
    expect(hueFor("abc")).toBe(hueFor("abc"));
  });

  it("produces different hues for different ids", () => {
    expect(hueFor("abc")).not.toBe(hueFor("xyz"));
  });

  it("stays within the 0–359 hue range", () => {
    for (const id of ["a", "listing-1", "019f8584-c0d2-7eae", ""]) {
      const hue = hueFor(id);
      expect(hue).toBeGreaterThanOrEqual(0);
      expect(hue).toBeLessThan(360);
    }
  });
});

describe("PropertyThumb", () => {
  it("shows the formatted price", () => {
    render(<PropertyThumb property={makeProperty()} />);
    expect(screen.getByText("$1,895,000")).toBeInTheDocument();
  });

  it("says so when there is no price rather than showing $0", () => {
    // A missing price is "on application", not free. Rendering $0 would be a
    // factual claim about the listing.
    render(<PropertyThumb property={makeProperty({ price: null })} />);
    expect(screen.getByText("Price on application")).toBeInTheDocument();
    expect(screen.queryByText("$0")).not.toBeInTheDocument();
  });

  it("renders the status badge with the API's snake_case value", () => {
    render(<PropertyThumb property={makeProperty({ status: "off_market" })} />);
    expect(screen.getByText("Off market")).toBeInTheDocument();
  });

  it("shows the cover photo when the listing has one", () => {
    const { container } = render(
      <PropertyThumb
        property={makeProperty({
          cover_attachment_id: "019f8584-c0d2-7eae-885a-000000000001",
          cover_url: "https://files.example.test/cover.webp?sig=abc",
        })}
      />,
    );
    const image = container.querySelector("img");
    expect(image).toHaveAttribute(
      "src",
      "https://files.example.test/cover.webp?sig=abc",
    );
  });

  it("falls back to the generated gradient with no cover", () => {
    // Every listing looked like this before photography existed, so the
    // fallback is a design, not an error state.
    const { container } = render(<PropertyThumb property={makeProperty()} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.firstElementChild?.getAttribute("style")).toContain(
      "linear-gradient",
    );
  });
});
