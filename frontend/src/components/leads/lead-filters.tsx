"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { ListFilter, Loader2, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const STAGES = ["new", "contacted", "qualified", "touring", "unqualified"];

function label(value: string): string {
  const spaced = value.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * Search and filter controls, driven through the URL.
 *
 * State lives in the query string rather than component state so a filtered
 * view is shareable, bookmarkable and survives a refresh — and so the server
 * component does the filtering rather than shipping every lead to the browser.
 */
export function LeadFilterBar() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [isPending, startTransition] = useTransition();

  const [search, setSearch] = useState(params.get("search") ?? "");

  function apply(next: Record<string, string | null>) {
    const updated = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === "") {
        updated.delete(key);
      } else {
        updated.set(key, value);
      }
    }
    // Any filter change invalidates the cursor — it points into the old
    // result set and would silently skip rows.
    updated.delete("cursor");

    startTransition(() => {
      router.push(`${pathname}?${updated.toString()}`);
    });
  }

  // Debounced so typing does not fire a request per keystroke.
  useEffect(() => {
    const current = params.get("search") ?? "";
    if (search === current) return;

    const timer = setTimeout(() => apply({ search: search || null }), 350);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const stage = params.get("stage") ?? "all";

  return (
    <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div className="flex flex-1 flex-wrap items-center gap-2">
        <div className="relative w-full min-w-0 sm:w-72">
          {isPending ? (
            <Loader2 className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 animate-spin text-muted-foreground" />
          ) : (
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          )}
          <Input
            placeholder="Search leads by name, email or location…"
            className="pl-9"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>

        <Select
          value={stage}
          onValueChange={(value) =>
            apply({ stage: value === "all" ? null : value })
          }
        >
          <SelectTrigger size="sm" className="w-[150px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All stages</SelectItem>
            {STAGES.map((option) => (
              <SelectItem key={option} value={option}>
                {label(option)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {(params.get("search") || params.get("stage")) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSearch("");
              apply({ search: null, stage: null });
            }}
          >
            <ListFilter className="size-4" />
            Clear
          </Button>
        )}
      </div>
    </div>
  );
}
