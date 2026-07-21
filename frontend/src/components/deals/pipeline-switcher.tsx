"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTransition } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Pipeline } from "@/lib/api/types";

/**
 * Switch the board between pipelines.
 *
 * Driven through the URL like every other filter, so a particular board is
 * shareable and survives a refresh. Rendered only when a workspace has more
 * than one pipeline — a lone default needs no chooser.
 */
export function PipelineSwitcher({
  pipelines,
  current,
}: {
  pipelines: Pipeline[];
  current: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [isPending, startTransition] = useTransition();

  return (
    <Select
      value={current}
      disabled={isPending}
      onValueChange={(next) => {
        if (!next) return;
        const updated = new URLSearchParams(params.toString());
        updated.set("pipeline_id", next);
        startTransition(() => {
          router.push(`${pathname}?${updated.toString()}`);
        });
      }}
    >
      <SelectTrigger size="sm" className="w-[180px]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {pipelines.map((pipeline) => (
          <SelectItem key={pipeline.id} value={pipeline.id}>
            {pipeline.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
