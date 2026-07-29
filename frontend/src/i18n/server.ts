import "server-only";

/**
 * Server-side i18n.
 *
 * Server Components cannot use the React context hook, so they read the locale
 * from the request cookie and resolve a `t` directly. This mirrors the client
 * runtime exactly — same dictionaries, same fallback chain — so a string reads
 * identically whether its component renders on the server or the client.
 *
 * Reading `cookies()` opts the caller into dynamic rendering, which every
 * authenticated page in this app already is (it fetches the session).
 */

import { cookies } from "next/headers";

import { LOCALE_COOKIE, resolveLocale, type Locale } from "./config";
import { baseDictionary, loadDictionary } from "./dictionaries";
import { createTranslator, type TranslateFn } from "./translate";

/** The locale for the current request, from the cookie (defaulting to `en`). */
export async function getLocale(): Promise<Locale> {
  const store = await cookies();
  return resolveLocale(store.get(LOCALE_COOKIE)?.value);
}

/**
 * A bound `t` for the current request's locale.
 *
 * ```tsx
 * export default async function Page() {
 *   const t = await getTranslations();
 *   return <h1>{t("leads.title")}</h1>;
 * }
 * ```
 */
export async function getTranslations(locale?: Locale): Promise<TranslateFn> {
  const resolved = locale ?? (await getLocale());
  const dictionary = await loadDictionary(resolved);
  return createTranslator(dictionary, baseDictionary);
}
