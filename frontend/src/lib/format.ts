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
