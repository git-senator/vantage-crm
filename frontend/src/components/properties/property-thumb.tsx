import { StatusBadge } from "@/components/shared/status-badge";
import { formatCurrency } from "@/lib/format";
import type { Property } from "@/lib/api/types";

/**
 * Deterministic hue from the listing id.
 *
 * The fallback when a listing has no cover photo — which is every listing
 * until someone uploads one, so it stays a first-class look rather than an
 * error state. Deriving the hue from the id keeps a listing the same colour
 * across renders, pages and filters; the prototype cycled an array by list
 * index, so a card changed colour as soon as the list was filtered.
 */
export function hueFor(id: string): number {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return hash;
}

export function PropertyThumb({
  property,
  className,
  priceLabel,
  statusLabel,
}: {
  property: Property;
  className?: string;
  /**
   * The price as the page wants it worded — translated, and carrying "/month"
   * on a rental. Passed in rather than resolved here: this component sits
   * inside server pages that already hold the translator, and reaching for one
   * would make it a client component and drag a language provider into every
   * test that renders a card.
   */
  priceLabel?: string;
  /** The status in words, translated. Same reasoning as `priceLabel`. */
  statusLabel?: string;
}) {
  const hue = hueFor(property.id);
  const cover = property.cover_url;

  return (
    <div
      className={className ?? "relative aspect-[16/10] overflow-hidden"}
      style={
        cover
          ? undefined
          : {
              backgroundImage: `linear-gradient(145deg, oklch(0.78 0.11 ${hue}), oklch(0.55 0.14 ${hue + 25}))`,
            }
      }
    >
      {cover ? (
        <>
          {/* A plain <img>, not next/image: the URL is signed and expires, so
              it can be neither optimised at build time nor cached by the image
              proxy under a stable key. */}
          <img
            src={cover}
            alt=""
            loading="lazy"
            decoding="async"
            className="absolute inset-0 size-full object-cover"
          />
          {/* Keeps the status pill and the price legible over a bright sky. */}
          <div
            aria-hidden
            className="absolute inset-0 bg-gradient-to-t from-black/55 via-black/10 to-black/25"
          />
        </>
      ) : (
        <>
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
        </>
      )}

      <div className="absolute top-3 left-3 flex gap-1.5">
        <StatusBadge
          status={property.status}
          label={statusLabel}
          className="bg-white/90 text-neutral-900 backdrop-blur-sm dark:bg-neutral-900/85 dark:text-white"
        />
      </div>

      <p className="absolute bottom-3 left-3 text-lg font-semibold text-white drop-shadow-sm">
        {priceLabel ??
          (property.price
            ? formatCurrency(Number(property.price))
            : "Price on application")}
      </p>
    </div>
  );
}
