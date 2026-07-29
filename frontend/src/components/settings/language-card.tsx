"use client";

import { Globe } from "lucide-react";
import { toast } from "sonner";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { isLocale, LOCALE_META, type Locale } from "@/i18n/config";
import { useLanguage } from "@/i18n/language-provider";

/**
 * The language selector, in Settings › General.
 *
 * Switching writes the preference cookie and lazy-loads the chosen dictionary
 * (both handled by `setLocale`), so the whole UI re-renders in the new language
 * immediately — no reload, no backend call. The choice is per-user and survives
 * across sessions via the cookie the server reads on the next request.
 */
export function LanguageCard() {
  const { locale, setLocale, t } = useLanguage();

  function handleChange(value: Locale | null) {
    if (!isLocale(value) || value === locale) return;
    setLocale(value);
    toast.success(t("settings.languageSaved"));
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Globe className="size-4 text-primary" />
          {t("settings.languageTitle")}
        </CardTitle>
        <CardDescription>{t("settings.languageDesc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <Label htmlFor="language">{t("settings.language")}</Label>
        <Select value={locale} onValueChange={handleChange}>
          <SelectTrigger id="language" className="sm:w-72">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.values(LOCALE_META)).map((meta) => (
              <SelectItem key={meta.locale} value={meta.locale}>
                <span className="mr-2">{meta.flag}</span>
                {meta.nativeName}
                {meta.nativeName !== meta.englishName && (
                  <span className="ml-1.5 text-muted-foreground">
                    · {meta.englishName}
                  </span>
                )}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-sm text-muted-foreground">
          {t("settings.languageHint")}
        </p>
      </CardContent>
    </Card>
  );
}
