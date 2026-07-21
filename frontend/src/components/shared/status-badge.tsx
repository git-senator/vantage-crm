import { cn } from "@/lib/utils";
import { titleize } from "@/lib/format";

export type Tone = "neutral" | "info" | "success" | "warning" | "danger" | "brand";

const toneClasses: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground",
  info: "bg-info/12 text-info dark:bg-info/20",
  success: "bg-success/12 text-success dark:bg-success/20",
  warning: "bg-warning/18 text-warning-foreground dark:bg-warning/20 dark:text-warning",
  danger: "bg-destructive/10 text-destructive dark:bg-destructive/20",
  brand: "bg-primary/10 text-primary dark:bg-primary/20",
};

const dotClasses: Record<Tone, string> = {
  neutral: "bg-muted-foreground/60",
  info: "bg-info",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-destructive",
  brand: "bg-primary",
};

/**
 * Every status vocabulary in the app funnels through this map so a given
 * meaning always gets the same colour, whatever entity it belongs to.
 */
const statusTones: Record<string, Tone> = {
  // leads
  new: "info",
  contacted: "brand",
  qualified: "success",
  touring: "warning",
  unqualified: "neutral",
  // temperature
  hot: "danger",
  warm: "warning",
  cold: "info",
  // clients — snake_case, matching the API's wire format
  active: "success",
  under_contract: "warning",
  dormant: "neutral",
  past: "neutral",
  closed: "neutral",
  // properties — snake_case, matching the API's wire format
  pending: "warning",
  sold: "brand",
  off_market: "neutral",
  coming_soon: "info",
  // deals
  qualification: "neutral",
  showing: "info",
  offer: "warning",
  closing: "brand",
  "closed-won": "success",
  // tasks
  todo: "neutral",
  "in-progress": "info",
  blocked: "danger",
  done: "success",
  // priority
  low: "neutral",
  medium: "info",
  high: "warning",
  urgent: "danger",
  // documents
  draft: "neutral",
  "awaiting-signature": "warning",
  signed: "success",
  expired: "danger",
};

export function StatusBadge({
  status,
  label,
  tone,
  dot = true,
  className,
}: {
  status: string;
  label?: string;
  tone?: Tone;
  dot?: boolean;
  className?: string;
}) {
  const resolved = tone ?? statusTones[status] ?? "neutral";

  return (
    <span
      className={cn(
        "inline-flex h-[22px] items-center gap-1.5 rounded-full px-2 text-xs font-medium whitespace-nowrap",
        toneClasses[resolved],
        className,
      )}
    >
      {dot && (
        <span className={cn("size-1.5 rounded-full", dotClasses[resolved])} />
      )}
      {label ?? titleize(status)}
    </span>
  );
}
