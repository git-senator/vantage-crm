"use client";

import { Languages, TriangleAlert } from "lucide-react";

import { useTranslation } from "@/i18n/language-provider";
import { LOCALE_META, type Locale } from "@/i18n/config";
import type { Property } from "@/lib/api/types";

/**
 * Says where a listing's text came from, when that is not obvious.
 *
 * Three states worth telling an agent about, and one worth staying quiet for:
 *
 *   * the text is a machine translation nobody has read — say so, because this
 *     is what an agent is about to paste into a message to a client;
 *   * a person translated it and the listing has changed since — say so
 *     louder, because the text is now describing a property that moved;
 *   * no translation exists and the source language is showing through — say
 *     which language it is, so a reader who expected Portuguese understands
 *     why they are looking at Russian rather than assuming the page is broken.
 *
 * When the text is in the language asked for and a person wrote it, this
 * renders nothing. A badge on every listing is a badge nobody reads.
 */
export function TranslationNotice({ property }: { property: Property }) {
  const { t } = useTranslation();

  const untranslated = property.content_locale === property.source_locale;
  const sourceName = LOCALE_META[property.source_locale as Locale]?.nativeName;

  if (property.content_is_stale) {
    return (
      <p className="mt-3 flex items-start gap-2 text-xs text-amber-700 dark:text-amber-500">
        <TriangleAlert className="mt-px size-3.5 shrink-0" />
        {t("body.trStale")}
      </p>
    );
  }

  if (untranslated) {
    return (
      <p className="mt-3 flex items-start gap-2 text-xs text-muted-foreground">
        <Languages className="mt-px size-3.5 shrink-0" />
        {t("body.trOriginalOnly", { language: sourceName ?? property.source_locale })}
      </p>
    );
  }

  if (property.content_is_machine) {
    return (
      <p className="mt-3 flex items-start gap-2 text-xs text-muted-foreground">
        <Languages className="mt-px size-3.5 shrink-0" />
        {t("body.trMachine")}
      </p>
    );
  }

  return null;
}
