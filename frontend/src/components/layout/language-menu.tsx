"use client";

import { Languages } from "lucide-react";

import {
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import { isLocale, LOCALE_META, type Locale } from "@/i18n/config";
import { useLanguage } from "@/i18n/language-provider";
import { cn } from "@/lib/utils";

/**
 * Quick language switcher for the user avatar dropdown.
 *
 * A submenu whose trigger shows the active language's flag, expanding to a
 * radio group of the supported languages. It reads and writes the same
 * `useLanguage` context the Settings selector uses, so the two controls are one
 * preference: switching here updates Settings (and the whole UI) at once, with
 * no reload, and persists via the cookie `setLocale` writes.
 *
 * Selecting a radio item closes the menu (base-ui closes on select), and the
 * active language is marked by the radio indicator.
 */
export function LanguageMenu() {
  const { locale, setLocale, t } = useLanguage();
  const active = LOCALE_META[locale];

  function handleChange(value: string) {
    if (isLocale(value) && value !== locale) {
      setLocale(value as Locale);
    }
  }

  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger>
        <Languages className="size-4" />
        {t("language.switcher")}
        <span className="ml-auto text-base leading-none" aria-hidden>
          {active.flag}
        </span>
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent className="min-w-52">
        <DropdownMenuRadioGroup value={locale} onValueChange={handleChange}>
          {Object.values(LOCALE_META).map((meta) => (
            <DropdownMenuRadioItem
              key={meta.locale}
              value={meta.locale}
              className={cn(locale === meta.locale && "font-medium")}
            >
              <span className="mr-1.5 text-base leading-none" aria-hidden>
                {meta.flag}
              </span>
              {meta.nativeName}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  );
}
