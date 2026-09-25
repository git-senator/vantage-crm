import { describe, expect, it } from "vitest";

import { percentToRate, rateToPercent } from "@/lib/commission";

describe("percentToRate", () => {
  it("moves the point two places, exactly", () => {
    expect(percentToRate("5")).toBe("0.05");
    expect(percentToRate("2.5")).toBe("0.025");
    expect(percentToRate("7.25")).toBe("0.0725");
    expect(percentToRate("0.5")).toBe("0.005");
  });

  it("keeps the ten-fold neighbours apart", () => {
    expect(percentToRate("5")).not.toBe(percentToRate("0.5"));
  });

  it("does not introduce the float noise a division would", () => {
    // 7.25 / 100 === 0.07250000000000001 in JS.
    expect(percentToRate("7.25")).toBe("0.0725");
    expect(percentToRate("2.9")).toBe("0.029");
  });

  it("handles the ends of the range", () => {
    expect(percentToRate("0")).toBe("0");
    expect(percentToRate("100")).toBe("1");
  });

  it("treats a comma like a point, as a Russian keyboard produces", () => {
    expect(percentToRate("2,5")).toBe("0.025");
  });

  it("returns null for an empty field rather than zero", () => {
    expect(percentToRate("")).toBeNull();
    expect(percentToRate("   ")).toBeNull();
  });

  it("hands anything unparseable to the server untouched", () => {
    expect(percentToRate("abc")).toBe("abc");
  });
});

describe("rateToPercent", () => {
  it("is the inverse, so an edit form shows what was typed", () => {
    expect(rateToPercent("0.05")).toBe("5");
    expect(rateToPercent("0.025")).toBe("2.5");
    expect(rateToPercent("0.0725")).toBe("7.25");
    expect(rateToPercent("1")).toBe("100");
  });

  it("survives the trailing zeros a decimal column returns", () => {
    expect(rateToPercent("0.0500")).toBe("5");
    expect(rateToPercent("0.0000")).toBe("0");
  });

  it("shows nothing when there is no rate", () => {
    expect(rateToPercent(null)).toBe("");
    expect(rateToPercent(undefined)).toBe("");
    expect(rateToPercent("")).toBe("");
  });

  it("round-trips every rate the form can produce", () => {
    for (const percent of ["0", "0.5", "2.5", "5", "7.25", "10", "100"]) {
      expect(rateToPercent(percentToRate(percent))).toBe(percent);
    }
  });
});
