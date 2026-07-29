"use client";

/**
 * The client-side language runtime.
 *
 * Holds the active locale and its resolved dictionary, and hands components a
 * `t()` bound to both (with English fallback). The provider is mounted once, at
 * the root, with `initialLocale` resolved from the cookie on the server — so
 * the first paint already reflects the user's choice and `<html lang>` matches.
 *
 * Non-English dictionaries load lazily: until the chunk arrives, `t` resolves
 * against the base dictionary, so text is always English-then-translated rather
 * than blank. This also keeps the server and client's first render identical
 * (both use the base dictionary), avoiding a hydration mismatch.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  DEFAULT_LOCALE,
  LOCALE_COOKIE,
  LOCALE_COOKIE_MAX_AGE,
  LOCALE_META,
  LOCALES,
  type Locale,
} from "./config";
import {
  baseDictionary,
  loadDictionary,
  peekDictionary,
} from "./dictionaries";
import type { Dictionary } from "./locales/en";
import { createTranslator, type TranslateFn } from "./translate";

interface LanguageContextValue {
  locale: Locale;
  /** The active dictionary — base until a lazy locale finishes loading. */
  dictionary: Dictionary;
  /** Bound translator: `t("nav.leads")`, `t("tables.page", { current, total })`. */
  t: TranslateFn;
  /** Switch language: persists the cookie and lazy-loads the dictionary. */
  setLocale: (locale: Locale) => void;
  /** True while a newly selected dictionary chunk is downloading. */
  isLoading: boolean;
  /** Every locale the UI offers, for a switcher. */
  locales: typeof LOCALES;
}

const LanguageContext = createContext<LanguageContextValue | null>(null);

/** Persist the choice where the server can read it on the next request. */
function writeLocaleCookie(locale: Locale): void {
  if (typeof document === "undefined") return;
  document.cookie =
    `${LOCALE_COOKIE}=${locale}; path=/; max-age=${LOCALE_COOKIE_MAX_AGE}; samesite=lax`;
}

export function LanguageProvider({
  initialLocale = DEFAULT_LOCALE,
  children,
}: {
  initialLocale?: Locale;
  children: ReactNode;
}) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  // Bumped when an async dictionary load completes, to re-derive the dictionary
  // from the (now populated) module cache without ever calling setState in the
  // load effect's synchronous body.
  const [loadedVersion, setLoadedVersion] = useState(0);

  // The dictionary is *derived*, not stored: whatever is resolvable
  // synchronously (base for `en`, or a cached chunk) and the base dictionary as
  // the universal fallback while a chunk is still in flight. Recomputed when the
  // locale changes or a load finishes.
  const dictionary = useMemo<Dictionary>(
    () => peekDictionary(locale) ?? baseDictionary,
    // loadedVersion invalidates the memo once a load populates the cache.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [locale, loadedVersion],
  );

  const isLoading = useMemo(
    () => peekDictionary(locale) === undefined,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [locale, loadedVersion],
  );

  // Kick off the lazy load when a locale is not yet cached. State is only
  // touched inside the async resolution — never synchronously — so this does
  // not cascade renders. The `active` guard drops a stale switch.
  useEffect(() => {
    if (peekDictionary(locale)) return;

    let active = true;
    loadDictionary(locale).then(() => {
      if (active) setLoadedVersion((version) => version + 1);
    });

    return () => {
      active = false;
    };
  }, [locale]);

  // Keep the document language attribute in sync for a11y and Intl, even
  // between full loads (a client-side switch does not re-run the server layout).
  useEffect(() => {
    if (typeof document !== "undefined") {
      document.documentElement.lang = LOCALE_META[locale].htmlLang;
    }
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    writeLocaleCookie(next);
    setLocaleState(next);
  }, []);

  const t = useMemo(
    () => createTranslator(dictionary, baseDictionary),
    [dictionary],
  );

  const value = useMemo<LanguageContextValue>(
    () => ({ locale, dictionary, t, setLocale, isLoading, locales: LOCALES }),
    [locale, dictionary, t, setLocale, isLoading],
  );

  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  );
}

function useLanguageContext(): LanguageContextValue {
  const context = useContext(LanguageContext);
  if (!context) {
    throw new Error("useLanguage must be used within a LanguageProvider");
  }
  return context;
}

/** Full language runtime: locale, switcher and loading state. */
export function useLanguage(): LanguageContextValue {
  return useLanguageContext();
}

/**
 * The common case: just the translator.
 *
 * `const { t } = useTranslation()` in any client component under the provider.
 */
export function useTranslation(): { t: TranslateFn; locale: Locale } {
  const { t, locale } = useLanguageContext();
  return { t, locale };
}
