/** Display formatters. Locale is pinned so SSR and client output match. */

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const usdCents = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

const compact = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

export const formatCurrency = (value: number) => usd.format(value);
export const formatCurrencyCents = (value: number) => usdCents.format(value);
export const formatCompact = (value: number) => compact.format(value);

/** $1.9M / $890K — used wherever a full price would blow out a column. */
export function formatPrice(value: number) {
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${Math.round(value / 1_000)}K`;
  return usd.format(value);
}

export function formatRange([low, high]: [number, number]) {
  return `${formatPrice(low)} – ${formatPrice(high)}`;
}

export const formatNumber = (value: number) =>
  new Intl.NumberFormat("en-US").format(value);

/** How many square feet make a square metre. */
const SQFT_PER_M2 = 10.7639;

export type MeasurementSystem = "metric" | "imperial";

/**
 * An area, in the units the workspace reads.
 *
 * Areas are stored in square feet — that is the column's name, and a column
 * named `square_feet` holding square metres is a lie waiting to be found.
 * Which unit a person *sees* is a workspace preference, and for a brokerage
 * selling in Brazil the answer is square metres: every document, every
 * developer's brochure and every conversation is in m².
 *
 * The round trip is lossless enough to be invisible — 350 m² stored as 3767
 * sqft reads back as 350 — because whole square metres are a coarser unit than
 * whole square feet.
 */
export function formatArea(
  squareFeet: number,
  system: MeasurementSystem = "metric",
): string {
  if (system === "imperial") return `${formatNumber(squareFeet)} sqft`;
  return `${formatNumber(Math.round(squareFeet / SQFT_PER_M2))} m²`;
}

/** Square metres typed by a person, in the unit the column stores. */
export function areaToSquareFeet(
  value: number,
  system: MeasurementSystem = "metric",
): number {
  return system === "imperial" ? value : Math.round(value * SQFT_PER_M2);
}

/** Square feet as stored, in the unit a person should be shown in an input. */
export function areaFromSquareFeet(
  squareFeet: number,
  system: MeasurementSystem = "metric",
): number {
  return system === "imperial"
    ? squareFeet
    : Math.round(squareFeet / SQFT_PER_M2);
}

/**
 * Turns "under-contract" or "under_contract" into "Under contract".
 *
 * Both separators, because the API speaks snake_case (`open_house`,
 * `under_contract`) while the original prototype fixtures used kebab-case.
 */
export function titleize(slug: string) {
  const spaced = slug.replace(/[-_]/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
