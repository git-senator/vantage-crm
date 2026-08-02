import type { TranslateFn } from "@/i18n/translate";

/**
 * Localizing server-provided display labels by their stable key.
 *
 * The analytics API sends English `label`/`stage_name` text alongside a stable
 * `key`. The frontend owns the translations, so these helpers map the key into
 * the active dictionary and fall back to the server's own text when a key has no
 * entry (a custom pipeline stage a workspace renamed keeps its own name). The
 * fallback is why `t` returning the key path unchanged is treated as "no
 * translation" rather than shown to the user.
 */

/** A metric's label from `metrics.<key>`, falling back to the server label. */
export function metricLabel(t: TranslateFn, key: string, fallback: string): string {
  const path = `metrics.${key}`;
  const value = t(path);
  return value === path ? fallback : value;
}

/** The default pipeline stages, English seed name → stable key. */
const STAGE_KEY: Record<string, string> = {
  Qualification: "qualification",
  Showing: "showing",
  "Offer submitted": "offer",
  "Under contract": "under_contract",
  Closing: "closing",
  "Closed won": "closed_won",
  "Closed lost": "closed_lost",
};

/** A pipeline stage's label from `stages.<key>`; custom names pass through. */
export function stageLabel(t: TranslateFn, name: string): string {
  const key = STAGE_KEY[name];
  if (!key) return name;
  const path = `stages.${key}`;
  const value = t(path);
  return value === path ? name : value;
}
