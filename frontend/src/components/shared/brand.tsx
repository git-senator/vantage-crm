import { cn } from "@/lib/utils";

/**
 * Wordmark plus glyph. The glyph is a stylised roofline over a rising bar:
 * property, and a pipeline climbing under it.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "grid size-8 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground shadow-sm",
        className,
      )}
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden
        className="size-[18px]"
        strokeWidth={2}
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M3 10.5 12 4l9 6.5" />
        <path d="M7 20v-5" />
        <path d="M12 20v-8" />
        <path d="M17 20v-3" />
      </svg>
    </span>
  );
}

export function BrandLockup({ className }: { className?: string }) {
  return (
    <span className={cn("flex items-center gap-2.5", className)}>
      <BrandMark />
      <span className="flex flex-col leading-none">
        <span className="text-[15px] font-semibold tracking-tight">ROSSA</span>
        <span className="mt-0.5 text-[11px] text-muted-foreground">CRM</span>
      </span>
    </span>
  );
}
