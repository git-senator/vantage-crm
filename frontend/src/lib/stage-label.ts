/**
 * Display name for a pipeline stage.
 *
 * Stage names are rows in the database, not dictionary entries: a workspace can
 * rename any of them, and the seeded ones arrive in English whatever language
 * the reader has chosen. So the seven seeded names get a translation each and
 * everything else falls through untouched — the only behaviour that serves a
 * Russian reader without overwriting the wording a team chose for itself.
 */

import type { TranslateFn } from "@/i18n/translate";

const SEED_KEYS: Record<string, string> = {
  Qualification: "pipelines.seedQualification",
  Showing: "pipelines.seedShowing",
  "Offer submitted": "pipelines.seedOfferSubmitted",
  "Under contract": "pipelines.seedUnderContract",
  Closing: "pipelines.seedClosing",
  "Closed won": "pipelines.seedClosedWon",
  "Closed lost": "pipelines.seedClosedLost",
};

export function stageLabel(name: string, t: TranslateFn): string {
  const key = SEED_KEYS[name];
  if (!key) return name;
  // A missing key resolves to the key itself; that means no translation, so
  // the stored name is the better thing to show.
  const translated = t(key);
  return translated === key ? name : translated;
}
