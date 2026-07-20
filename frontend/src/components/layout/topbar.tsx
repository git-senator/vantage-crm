"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bell, HelpCircle, Search, Sparkles } from "lucide-react";

import { StatusBadge } from "@/components/shared/status-badge";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { allNavItems } from "@/lib/nav";

/**
 * Receives only the unread *count*, never the notifications themselves.
 *
 * This component previously imported the whole `notifications` array to derive
 * one integer, which shipped every notification body to the browser. See
 * docs/SECURITY.md §4.
 */
export function Topbar({ unreadCount = 0 }: { unreadCount?: number }) {
  const pathname = usePathname();
  const current = allNavItems.find(
    (item) => pathname === item.href || pathname.startsWith(`${item.href}/`),
  );
  const unread = unreadCount;

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur-md">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-1 h-5" />

      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-medium">
          {current?.title ?? "Vantage"}
        </span>
        <StatusBadge
          status="prototype"
          label="Prototype"
          tone="neutral"
          dot={false}
          className="hidden sm:inline-flex"
        />
      </div>

      <div className="ml-auto flex items-center gap-1.5">
        {/* Visual affordance only — opens nothing. */}
        <button className="hidden h-8 items-center gap-2 rounded-lg border bg-card px-2.5 text-sm text-muted-foreground transition-colors hover:bg-muted md:flex">
          <Search className="size-4" />
          <span>Search</span>
          <kbd className="ml-6 rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px]">
            ⌘K
          </kbd>
        </button>

        <Tooltip>
          <TooltipTrigger
            render={
              <Button variant="ghost" size="icon" aria-label="Ask the assistant" render={<Link href="/ai-assistant" />}>
                <Sparkles className="size-4" />
              </Button>
            }
          />
          <TooltipContent>Ask Vantage AI</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Notifications, ${unread} unread`}
                className="relative"
                render={<Link href="/notifications" />}
              >
                <Bell className="size-4" />
                {unread > 0 && (
                  <span className="absolute top-1.5 right-1.5 grid size-4 place-items-center rounded-full bg-destructive text-[9px] font-semibold text-white">
                    {unread}
                  </span>
                )}
              </Button>
            }
          />
          <TooltipContent>Notifications</TooltipContent>
        </Tooltip>

        <ThemeToggle />

        <Button
          variant="ghost"
          size="icon"
          aria-label="Help"
          className="hidden sm:inline-flex"
        >
          <HelpCircle className="size-4" />
        </Button>
      </div>
    </header>
  );
}
