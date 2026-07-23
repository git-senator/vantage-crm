"use client";

import { useRouter, useSearchParams } from "next/navigation";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { PeriodName } from "@/lib/api/types";

const OPTIONS: { value: PeriodName; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "week", label: "This week" },
  { value: "month", label: "This month" },
  { value: "quarter", label: "This quarter" },
  { value: "year", label: "This year" },
];

/**
 * The window every analytics screen works in.
 *
 * Kept in the URL rather than in component state: a period is what the page is
 * *about*, so it should survive a refresh, be linkable, and appear in browser
 * history. Local state would make "send me that chart" impossible to answer.
 */
export function PeriodPicker({ value }: { value: PeriodName }) {
  const router = useRouter();
  const params = useSearchParams();

  function change(next: string | null) {
    if (!next) return;
    const query = new URLSearchParams(params.toString());
    query.set("period", next);
    router.push(`?${query.toString()}`);
  }

  return (
    <Select value={value} onValueChange={change}>
      <SelectTrigger size="sm" className="w-[150px]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {OPTIONS.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
