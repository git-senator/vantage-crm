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
import { titleize } from "@/lib/format";

const STATUSES = ["active", "pending", "sold", "off_market", "coming_soon"];
const TYPES = [
  "single_family",
  "condo",
  "townhouse",
  "multi_family",
  "land",
  "commercial",
];

/**
 * Price bands, as (label, min, max) rather than free numeric inputs.
 *
 * The prototype offered exactly these bands and they match how agents actually
 * filter. Kept as discrete options so the query stays index-friendly and there
 * is no "min above max" state to validate in the UI.
 */
const PRICE_BANDS = [
  { value: "any", label: "Any price", min: undefined, max: undefined },
  { value: "under-1m", label: "Under $1M", min: undefined, max: "1000000" },
  { value: "1m-2m", label: "$1M – $2M", min: "1000000", max: "2000000" },
  { value: "over-2m", label: "Over $2M", min: "2000000", max: undefined },
] as const;

/** Which band the current URL represents, so the Select stays in sync. */
export function bandFor(
  min: string | null,
  max: string | null,
): (typeof PRICE_BANDS)[number]["value"] {
  const match = PRICE_BANDS.find(
    (band) => (band.min ?? null) === min && (band.max ?? null) === max,
  );
  return match?.value ?? "any";
}

/**
 * Search and filter controls, driven through the URL.
 *
 * State lives in the query string rather than component state so a filtered
 * view is shareable, bookmarkable and survives a refresh — and so the server
 * component does the filtering rather than shipping every listing to the
 * browser.
 */
export function PropertyFilterBar({
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

  const status = params.get("status") ?? "all";
  const propertyType = params.get("property_type") ?? "any";
  const band = bandFor(params.get("min_price"), params.get("max_price"));
  const isFiltered = Boolean(
    params.get("search") ||
      params.get("status") ||
      params.get("property_type") ||
      params.get("min_price") ||
      params.get("max_price"),
  );

  return (
    <div className="space-y-4">
      <Tabs
        value={status}
        onValueChange={(value) =>
          apply({ status: value === "all" ? null : value })
        }
      >
        <TabsList>
          <TabsTrigger value="all">All</TabsTrigger>
          {STATUSES.map((option) => (
            <TabsTrigger key={option} value={option}>
              {titleize(option)}
              {counts?.[option] ? (
                <span className="tabular ml-1.5 text-xs text-muted-foreground">
                  {counts[option]}
                </span>
              ) : null}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <div className="flex flex-1 flex-wrap items-center gap-2">
        <div className="relative w-full min-w-0 sm:w-80">
          {isPending ? (
            <Loader2 className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 animate-spin text-muted-foreground" />
          ) : (
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          )}
          <Input
            placeholder="Search by address, MLS ID or neighbourhood…"
            className="pl-9"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>

        <Select
          value={propertyType}
          onValueChange={(value) =>
            apply({ property_type: value === "any" ? null : value })
          }
        >
          <SelectTrigger size="sm" className="w-[160px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="any">Any type</SelectItem>
            {TYPES.map((option) => (
              <SelectItem key={option} value={option}>
                {titleize(option)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={band}
          onValueChange={(value) => {
            const chosen =
              PRICE_BANDS.find((entry) => entry.value === value) ??
              PRICE_BANDS[0];
            apply({
              min_price: chosen.min ?? null,
              max_price: chosen.max ?? null,
            });
          }}
        >
          <SelectTrigger size="sm" className="w-[150px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PRICE_BANDS.map((entry) => (
              <SelectItem key={entry.value} value={entry.value}>
                {entry.label}
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
              apply({
                search: null,
                status: null,
                property_type: null,
                min_price: null,
                max_price: null,
              });
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
