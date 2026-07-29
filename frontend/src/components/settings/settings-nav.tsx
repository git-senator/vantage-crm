"use client";

import { useState } from "react";
import {
  Globe,
  Plug,
  ShieldCheck,
  Sparkles,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";

import { useTranslation } from "@/i18n/language-provider";
import { cn } from "@/lib/utils";

/**
 * The Settings section rail. Previously inert buttons — now each scrolls to its
 * card and marks itself active. Anchors by element id (Settings is one long
 * page, not separate routes).
 *
 * Self-contained on purpose: a server component cannot hand icon *functions* to
 * a client component (they are not serializable), so the section list and its
 * labels live here, translated through the hook.
 */
const SECTIONS: { id: string; icon: LucideIcon; labelKey: string }[] = [
  { id: "language", icon: Globe, labelKey: "settings.languageTitle" },
  { id: "general", icon: UserCog, labelKey: "settings.sectionGeneral" },
  { id: "ai", icon: Sparkles, labelKey: "settings.sectionAi" },
  { id: "team", icon: Users, labelKey: "settings.sectionTeam" },
  { id: "integrations", icon: Plug, labelKey: "settings.sectionIntegrations" },
  { id: "security", icon: ShieldCheck, labelKey: "settings.sectionSecurity" },
];

export function SettingsNav() {
  const { t } = useTranslation();
  const [active, setActive] = useState(SECTIONS[0].id);

  function jump(id: string) {
    setActive(id);
    document
      .getElementById(id)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <nav className="lg:sticky lg:top-20 lg:self-start">
      <ul className="flex gap-1 overflow-x-auto lg:flex-col lg:overflow-visible">
        {SECTIONS.map((section) => (
          <li key={section.id}>
            <button
              onClick={() => jump(section.id)}
              className={cn(
                "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm whitespace-nowrap transition-colors",
                active === section.id
                  ? "bg-accent font-medium text-accent-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <section.icon className="size-4 shrink-0" />
              {t(section.labelKey)}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
