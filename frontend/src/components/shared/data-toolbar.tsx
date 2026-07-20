import { ListFilter, Search, SlidersHorizontal } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/**
 * Search + filter chrome shared by every list view. Inert by design — this is a
 * visual prototype, so nothing here is wired to state.
 */
export function DataToolbar({
  placeholder = "Search…",
  filters,
  actions,
  className,
}: {
  placeholder?: string;
  filters?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between",
        className,
      )}
    >
      <div className="flex flex-1 flex-wrap items-center gap-2">
        <div className="relative w-full min-w-0 sm:w-72">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input placeholder={placeholder} className="pl-9" />
        </div>
        {filters}
        <Button variant="outline" size="sm" className="gap-1.5">
          <ListFilter className="size-4" />
          <span className="hidden sm:inline">Filters</span>
        </Button>
      </div>

      <div className="flex items-center gap-2">
        {actions}
        <Button variant="outline" size="icon-sm" aria-label="View options">
          <SlidersHorizontal className="size-4" />
        </Button>
      </div>
    </div>
  );
}
