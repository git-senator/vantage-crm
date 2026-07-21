import { describe, expect, it } from "vitest";

import { bandFor } from "@/components/properties/property-filters";

/**
 * The price Select is driven from the URL, not from component state, so the
 * mapping back from (min_price, max_price) to a band has to be exact. If it
 * misses, the control silently snaps to "Any price" while the list stays
 * filtered — the filter looks cleared but isn't.
 */
describe("bandFor", () => {
  it("maps an unfiltered URL to the any band", () => {
    expect(bandFor(null, null)).toBe("any");
  });

  it("maps a max-only filter to the under band", () => {
    expect(bandFor(null, "1000000")).toBe("under-1m");
  });

  it("maps a bounded range to the middle band", () => {
    expect(bandFor("1000000", "2000000")).toBe("1m-2m");
  });

  it("maps a min-only filter to the over band", () => {
    expect(bandFor("2000000", null)).toBe("over-2m");
  });

  it("falls back to any for a hand-edited range that matches no band", () => {
    // Someone can always type ?min_price=1234 into the URL. The control must
    // not throw, and must not claim a band it is not on.
    expect(bandFor("1234", "5678")).toBe("any");
  });

  it("does not confuse a partial match for a full one", () => {
    // Same min as the 1m-2m band but no max: that is the over-2m shape only
    // if the min matches, which it does not.
    expect(bandFor("1000000", null)).toBe("any");
  });
});
