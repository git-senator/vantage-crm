import { describe, expect, it } from "vitest";

import { toQuery } from "@/lib/api/query";

/**
 * These are contract tests, not formatting tests.
 *
 * The omission rules here are an agreement with the backend: `?status=` is a
 * 422 server-side because Pydantic validates the empty string against a
 * Literal, while an absent `status` means "no filter". Getting this wrong
 * produces a page that looks broken for reasons nothing in the UI explains.
 */
describe("toQuery", () => {
  it("returns an empty string when there is nothing to send", () => {
    expect(toQuery({})).toBe("");
  });

  it("omits undefined, null and empty-string values", () => {
    expect(
      toQuery({ a: undefined, b: null, c: "", d: "kept" }),
    ).toBe("?d=kept");
  });

  it("keeps zero, which is a meaningful filter value", () => {
    // `min_bedrooms=0` means studios included. A truthiness check would drop
    // it and silently widen the filter.
    expect(toQuery({ min_bedrooms: 0 })).toBe("?min_bedrooms=0");
  });

  it("keeps false", () => {
    expect(toQuery({ archived: false })).toBe("?archived=false");
  });

  it("prefixes with ? only when there is a query", () => {
    expect(toQuery({ a: undefined })).toBe("");
    expect(toQuery({ a: 1 })).toBe("?a=1");
  });

  it("percent-encodes values that would otherwise break the URL", () => {
    expect(toQuery({ search: "1428 Sanchez & Co" })).toBe(
      "?search=1428+Sanchez+%26+Co",
    );
  });

  it("preserves decimal strings exactly", () => {
    // Prices travel as strings end to end so NUMERIC precision survives.
    // Any coercion through a JS number here would round the value.
    expect(toQuery({ min_price: "1000000.55" })).toBe(
      "?min_price=1000000.55",
    );
  });
});
