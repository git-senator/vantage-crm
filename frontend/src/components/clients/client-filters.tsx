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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const TYPES = ["buyer", "seller", "investor", "landlord", "tenant", "other"];
const STATUSES = ["active", "under_contract", "dormant", "past"];

function label(value: string): string {
  const spaced = value.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * Search and filter controls, driven through the URL.
 *
 * State lives in the query string rather than component state so a filtered
 * view is shareable, bookmarkable and survives a refresh — and so the server
 * component does the filtering rather than shipping every client to the
 * browser.
 *
 * The prototype's tab bar is preserved as the type filter, so the design is
 * unchanged but the tabs now do something.
 */
export function ClientFilterBar({
  counts,
}: {
  counts?: Record<string, number>;
}) {
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

  const type = params.get("type") ?? "all";
  const status = params.get("status") ?? "all";
  const isFiltered = Boolean(
    params.get("search") || params.get("type") || params.get("status"),
  );

  return (
    <div className="space-y-4">
      <Tabs
        value={type}
        onValueChange={(value) => apply({ type: value === "all" ? null : value })}
      >
        <TabsList>
          <TabsTrigger value="all">All</TabsTrigger>
          {TYPES.map((option) => (
            <TabsTrigger key={option} value={option}>
              {label(option)}
              {counts?.[option] ? (
                <span className="ml-1.5 text-xs text-muted-foreground tabular">
                  {counts[option]}
                </span>
              ) : null}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <div className="flex flex-1 flex-wrap items-center gap-2">
        <div className="relative w-full min-w-0 sm:w-72">
          {isPending ? (
            <Loader2 className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 animate-spin text-muted-foreground" />
          ) : (
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          )}
          <Input
            placeholder="Search clients by name, company, email or phone…"
            className="pl-9"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>

        <Select
          value={status}
          onValueChange={(value) =>
            apply({ status: value === "all" ? null : value })
          }
        >
          <SelectTrigger size="sm" className="w-[170px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {STATUSES.map((option) => (
              <SelectItem key={option} value={option}>
                {label(option)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {isFiltered && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSearch("");
              apply({ search: null, type: null, status: null });
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
