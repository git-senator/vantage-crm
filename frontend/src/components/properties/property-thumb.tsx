import { StatusBadge } from "@/components/shared/status-badge";
import { formatCurrency } from "@/lib/format";
import type { Property } from "@/lib/api/types";

/**
 * Deterministic hue from the listing id.
 *
 * Listings have no photography, so each card gets a generated gradient. The
 * prototype stored `hue` as a fixture field; deriving it from the id keeps a
 * listing the same colour across renders, pages and filters — the prototype
 * cycled an array by list index, so a card changed colour as soon as the list
 * was filtered.
 */
export function hueFor(id: string): number {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return hash;
}

export function PropertyThumb({
  property,
  className,
}: {
  property: Property;
  className?: string;
}) {
  const hue = hueFor(property.id);

  return (
    <div
      className={className ?? "relative aspect-[16/10] overflow-hidden"}
      style={{
        backgroundImage: `linear-gradient(145deg, oklch(0.78 0.11 ${hue}), oklch(0.55 0.14 ${hue + 25}))`,
      }}
    >
      <div
        aria-hidden
        className="absolute inset-0 opacity-20"
        style={{
          backgroundImage:
            "linear-gradient(to right, white 1px, transparent 1px), linear-gradient(to bottom, white 1px, transparent 1px)",
          backgroundSize: "32px 32px",
        }}
      />
      {/* Roofline motif echoes the brand glyph. */}
      <svg
        aria-hidden
        viewBox="0 0 100 60"
        className="absolute right-4 bottom-0 h-20 w-32 text-white/25"
        fill="currentColor"
      >
        <path d="M10 60V28L34 10l24 18v32H10Z" />
        <path d="M62 60V36l18-12 14 10v26H62Z" opacity="0.7" />
      </svg>

      <div className="absolute top-3 left-3 flex gap-1.5">
        <StatusBadge
          status={property.status}
          className="bg-white/90 text-neutral-900 backdrop-blur-sm dark:bg-neutral-900/85 dark:text-white"
        />
      </div>

      <p className="absolute bottom-3 left-3 text-lg font-semibold text-white drop-shadow-sm">
        {property.price
          ? formatCurrency(Number(property.price))
          : "Price on application"}
      </p>
    </div>
  );
}
