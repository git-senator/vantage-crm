/**
 * Internationalization configuration.
 *
 * The single source of truth for which languages the UI ships. Adding a
 * language is a three-line change here plus one dictionary file — see
 * `dictionaries.ts`. Nothing else in the app hard-codes a locale.
 *
 * This module is intentionally free of React and of `server-only`/`client`
 * markers so it can be imported from anywhere: the middleware-adjacent server
 * layout, the client provider, and the dictionary loaders all share it.
 */

/** Every locale the UI can render. `en` is the base and the fallback. */
export const LOCALES = ["en", "pt-BR", "ru"] as const;

export type Locale = (typeof LOCALES)[number];

/** The development default and the fallback for any missing translation. */
export const DEFAULT_LOCALE: Locale = "en";

/** The cookie the browser and the server share to remember the choice. */
export const LOCALE_COOKIE = "vg_locale";

/** A year — a language preference is not a session-scoped decision. */
export const LOCALE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

/**
 * Display metadata for the language switcher. `nativeName` is what a speaker of
 * that language expects to see; `englishName` labels it for everyone else.
 */
export interface LocaleMeta {
  locale: Locale;
  nativeName: string;
  englishName: string;
  /** BCP-47 tag for `<html lang>` and `Intl` formatting. */
  htmlLang: string;
  flag: string;
}

export const LOCALE_META: Record<Locale, LocaleMeta> = {
  en: {
    locale: "en",
    nativeName: "English",
    englishName: "English",
    htmlLang: "en",
    flag: "🇺🇸",
  },
  "pt-BR": {
    locale: "pt-BR",
    nativeName: "Português (Brasil)",
    englishName: "Portuguese (Brazil)",
    htmlLang: "pt-BR",
    flag: "🇧🇷",
  },
  ru: {
    locale: "ru",
    nativeName: "Русский",
    englishName: "Russian",
    htmlLang: "ru",
    flag: "🇷🇺",
  },
};

/** Narrow an arbitrary string (cookie value, query param) to a known locale. */
export function isLocale(value: string | null | undefined): value is Locale {
  return value != null && (LOCALES as readonly string[]).includes(value);
}

/** A known locale or the default — never throws, safe on untrusted input. */
export function resolveLocale(value: string | null | undefined): Locale {
  return isLocale(value) ? value : DEFAULT_LOCALE;
}
