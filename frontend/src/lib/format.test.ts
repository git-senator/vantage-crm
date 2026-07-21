import { describe, expect, it } from "vitest";

import { formatNumber, formatPrice, titleize } from "@/lib/format";

describe("titleize", () => {
  it("handles the API's snake_case", () => {
    expect(titleize("off_market")).toBe("Off market");
    expect(titleize("single_family")).toBe("Single family");
  });

  it("still handles the prototype's kebab-case", () => {
    // Deals and tasks fixtures have not been ported yet and still use hyphens.
    expect(titleize("closed-won")).toBe("Closed won");
  });

  it("leaves a single word alone apart from capitalising it", () => {
    expect(titleize("active")).toBe("Active");
  });

  it("does not crash on an empty string", () => {
    expect(titleize("")).toBe("");
  });
});

describe("formatPrice", () => {
  it("abbreviates millions to two decimals", () => {
    expect(formatPrice(1_895_000)).toBe("$1.90M");
  });

  it("abbreviates thousands", () => {
    expect(formatPrice(890_000)).toBe("$890K");
  });

  it("renders small amounts in full", () => {
    expect(formatPrice(500)).toBe("$500");
  });
});

describe("formatNumber", () => {
  it("groups thousands", () => {
    expect(formatNumber(2840)).toBe("2,840");
  });
});
