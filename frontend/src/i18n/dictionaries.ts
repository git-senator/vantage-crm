/**
 * Lazy dictionary loading.
 *
 * English is imported statically: it is the fallback, so it must always be
 * present synchronously — every other locale falls back to it while it loads
 * and for any key it is missing. The non-English dictionaries are `import()`ed
 * on demand, so a browser only downloads the language it actually shows. Each
 * dynamic import is its own chunk (see `next build` output).
 *
 * `loadDictionary` is memoised: switching away and back to a language does not
 * re-fetch its chunk, and concurrent callers share one in-flight promise.
 */

import { DEFAULT_LOCALE, type Locale } from "./config";
import en, { type Dictionary } from "./locales/en";

/** The always-available base dictionary and universal fallback. */
export const baseDictionary: Dictionary = en;

/** One dynamic import per non-base locale. English resolves synchronously. */
const loaders: Record<Locale, () => Promise<Dictionary>> = {
  en: () => Promise.resolve(en),
  "pt-BR": () => import("./locales/pt-BR").then((m) => m.default),
  ru: () => import("./locales/ru").then((m) => m.default),
};

const cache = new Map<Locale, Dictionary>();
const inflight = new Map<Locale, Promise<Dictionary>>();

/**
 * Resolve the dictionary for `locale`, loading its chunk once and caching it.
 *
 * On any load failure it degrades to the base dictionary rather than throwing —
 * a missing translation chunk must never take down the page.
 */
export async function loadDictionary(locale: Locale): Promise<Dictionary> {
  if (locale === DEFAULT_LOCALE) return en;

  const cached = cache.get(locale);
  if (cached) return cached;

  const existing = inflight.get(locale);
  if (existing) return existing;

  const promise = loaders[locale]()
    .then((dict) => {
      cache.set(locale, dict);
      inflight.delete(locale);
      return dict;
    })
    .catch((error) => {
      inflight.delete(locale);
      console.error(`i18n: failed to load "${locale}", using base`, error);
      return en;
    });

  inflight.set(locale, promise);
  return promise;
}

/** The dictionary if already loaded, else undefined — no fetch is triggered. */
export function peekDictionary(locale: Locale): Dictionary | undefined {
  if (locale === DEFAULT_LOCALE) return en;
  return cache.get(locale);
}
