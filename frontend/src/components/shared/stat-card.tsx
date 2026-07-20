import { ArrowDownRight, ArrowUpRight, type LucideIcon } from "lucide-react";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export interface Stat {
  label: string;
  value: string;
  delta?: number;
  hint?: string;
  icon?: LucideIcon;
  /** Set when a rising number is bad news — days on market, for instance. */
  invertDelta?: boolean;
}

export function StatCard({
  label,
  value,
  delta,
  hint,
  icon: Icon,
  invertDelta = false,
  className,
}: Stat & { className?: string }) {
  const isUp = (delta ?? 0) >= 0;
  const isGood = invertDelta ? !isUp : isUp;
  const DeltaIcon = isUp ? ArrowUpRight : ArrowDownRight;

  return (
    <Card className={cn("gap-0 p-5", className)}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        {Icon && (
          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground">
            <Icon className="size-4" />
          </span>
        )}
      </div>

      <p className="tabular mt-3 text-2xl font-semibold tracking-tight">
        {value}
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        {delta !== undefined && (
          <span
            className={cn(
              "tabular inline-flex items-center gap-0.5 font-medium",
              isGood ? "text-success" : "text-destructive",
            )}
          >
            <DeltaIcon className="size-3.5" />
            {Math.abs(delta)}%
          </span>
        )}
        {hint && <span className="text-muted-foreground">{hint}</span>}
      </div>
    </Card>
  );
}

export function StatGrid({
  stats,
  className,
}: {
  stats: Stat[];
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4",
        className,
      )}
    >
      {stats.map((stat) => (
        <StatCard key={stat.label} {...stat} />
      ))}
    </div>
  );
}
