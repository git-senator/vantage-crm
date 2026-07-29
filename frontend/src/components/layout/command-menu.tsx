"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Search } from "lucide-react";

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { useTranslation } from "@/i18n/language-provider";
import { navigation, secondaryNavigation } from "@/lib/nav";

/**
 * The topbar search — a real command palette, not a decorative box.
 *
 * Opens on ⌘K / Ctrl-K or by clicking the search affordance, and jumps to any
 * workspace destination. Titles come from the same translation keys the sidebar
 * uses, so the palette is localized for free.
 */
export function CommandMenu() {
  const router = useRouter();
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  function go(href: string) {
    setOpen(false);
    router.push(href);
  }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="hidden h-8 items-center gap-2 rounded-lg border bg-card px-2.5 text-sm text-muted-foreground transition-colors hover:bg-muted md:flex"
      >
        <Search className="size-4" />
        <span>{t("common.search")}</span>
        <kbd className="ml-6 rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px]">
          ⌘K
        </kbd>
      </button>

      <CommandDialog
        open={open}
        onOpenChange={setOpen}
        title={t("tooltips.commandMenu")}
        description={t("common.search")}
      >
        <Command>
          <CommandInput placeholder={`${t("common.search")}…`} />
          <CommandList>
          <CommandEmpty>{t("common.noResults")}</CommandEmpty>
          {navigation.map((group) => (
            <CommandGroup key={group.labelKey} heading={t(group.labelKey)}>
              {group.items.map((item) => (
                <CommandItem
                  key={item.href}
                  value={`${t(item.titleKey)} ${item.href}`}
                  onSelect={() => go(item.href)}
                >
                  <item.icon className="size-4" />
                  {t(item.titleKey)}
                </CommandItem>
              ))}
            </CommandGroup>
          ))}
          <CommandGroup heading={t("nav.settings")}>
            {secondaryNavigation.map((item) => (
              <CommandItem
                key={item.href}
                value={`${t(item.titleKey)} ${item.href}`}
                onSelect={() => go(item.href)}
              >
                <item.icon className="size-4" />
                {t(item.titleKey)}
              </CommandItem>
            ))}
          </CommandGroup>
          </CommandList>
        </Command>
      </CommandDialog>
    </>
  );
}
