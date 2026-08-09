"use client";

import { useTranslation } from "@/i18n/language-provider";

/**
 * A small blue dot meaning "something here is waiting on you".
 *
 * Renders nothing at zero — the dark state is the one people see most, and an
 * empty element in the badge slot would still take space and nudge the label.
 *
 * A bare coloured circle says nothing to a screen reader, so the count travels
 * with it as text. The circle itself is `aria-hidden`: the sentence is the
 * information, the dot is only how it looks.
 */
export function AttentionDot({ count }: { count: number }) {
  const { t } = useTranslation();
  if (count <= 0) return null;

  return (
    <span className="flex items-center">
      <span
        aria-hidden
        className="size-2 rounded-full bg-sky-500 ring-2 ring-sidebar shadow-[0_0_0_1px] shadow-sky-500/25"
      />
      <span className="sr-only">{t("nav.attentionWaiting", { n: count })}</span>
    </span>
  );
}
