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
import { titleize } from "@/lib/format";

const STATUSES = ["open", "won", "lost"];
const PRIORITIES = ["low", "medium", "high", "urgent"];

/**
 * Search and filter controls, driven through the URL.
 *
 * `status` is a derived field server-side — it resolves to the stage's terminal
 * flags — but from here it is just another query parameter, which is the point
 * of deriving it in the repository rather than in the client.
 */
export function DealFilterBar() {
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

  useEffect(() => {
    const current = params.get("search") ?? "";
    if (search === current) return;

    const timer = setTimeout(() => apply({ search: search || null }), 350);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const status = params.get("status") ?? "all";
  const priority = params.get("priority") ?? "all";
  const isFiltered = Boolean(
    params.get("search") || params.get("status") || params.get("priority"),
  );

  return (
    <div className="flex flex-1 flex-wrap items-center gap-2">
      <div className="relative w-full min-w-0 sm:w-80">
        {isPending ? (
          <Loader2 className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 animate-spin text-muted-foreground" />
        ) : (
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
        )}
        <Input
          placeholder="Search deals by title…"
          className="pl-9"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      <Select
        value={status}
        onValueChange={(value) => apply({ status: value === "all" ? null : value })}
      >
        <SelectTrigger size="sm" className="w-[140px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All statuses</SelectItem>
          {STATUSES.map((option) => (
            <SelectItem key={option} value={option}>
              {titleize(option)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={priority}
        onValueChange={(value) =>
          apply({ priority: value === "all" ? null : value })
        }
      >
        <SelectTrigger size="sm" className="w-[150px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">Any priority</SelectItem>
          {PRIORITIES.map((option) => (
            <SelectItem key={option} value={option}>
              {titleize(option)}
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
            apply({ search: null, status: null, priority: null });
          }}
        >
          <ListFilter className="size-4" />
          Clear
        </Button>
      )}
    </div>
  );
}
