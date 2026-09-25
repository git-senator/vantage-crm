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

/**
 * Same labels, addressed by the stage's key rather than its name.
 *
 * The timeline needs this: a stage-change activity stores the keys it moved
 * between, not the names, which is what lets an entry written months ago read
 * in today's language instead of the English it was filed in.
 */
const SEED_KEYS_BY_KEY: Record<string, string> = {
  qualification: "pipelines.seedQualification",
  showing: "pipelines.seedShowing",
  offer: "pipelines.seedOfferSubmitted",
  under_contract: "pipelines.seedUnderContract",
  closing: "pipelines.seedClosing",
  closed_won: "pipelines.seedClosedWon",
  closed_lost: "pipelines.seedClosedLost",
};

export function stageLabelByKey(
  key: string | null | undefined,
  t: TranslateFn,
): string | null {
  if (!key) return null;
  const dictKey = SEED_KEYS_BY_KEY[key];
  if (!dictKey) return null;
  const translated = t(dictKey);
  return translated === dictKey ? null : translated;
}
