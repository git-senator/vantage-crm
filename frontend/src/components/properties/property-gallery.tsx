"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Expand, X } from "lucide-react";

import { useTranslation } from "@/i18n/language-provider";
import type { PropertyPhoto } from "@/lib/api/types";

/**
 * The listing's photography: one lead image, the rest below it.
 *
 * Photos arrive already ordered and already signed — the server did both, so
 * this component fetches nothing and holds no credential of its own. The URLs
 * expire, which is why every image is a plain `<img>`: `next/image` would key
 * its cache on a URL that stops being valid, and serve a broken frame from it
 * later.
 *
 * The full-screen view is a plain overlay rather than the Dialog primitive. A
 * dialog traps focus around form controls; this is a picture with two arrows,
 * and the keyboard handling it actually needs — Escape to leave, arrows to
 * move — is the handler below.
 */
export function PropertyGallery({
  photos,
  title,
}: {
  photos: PropertyPhoto[];
  title: string;
}) {
  const { t } = useTranslation();
  const [index, setIndex] = useState(0);
  const [expanded, setExpanded] = useState(false);

  const count = photos.length;
  const go = useCallback(
    (step: number) => setIndex((current) => (current + step + count) % count),
    [count],
  );

  useEffect(() => {
    if (!expanded) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
      if (event.key === "ArrowLeft") go(-1);
      if (event.key === "ArrowRight") go(1);
    };
    window.addEventListener("keydown", onKey);
    // The page behind must not scroll while the overlay is open.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [expanded, go]);

  if (count === 0) return null;
  const current = photos[index];

  return (
    <div className="space-y-3">
      <div className="group relative aspect-[21/9] overflow-hidden rounded-xl border bg-muted">
        <img
          src={current.url}
          alt={t("body.pgPhotoOf", { n: index + 1, total: count })}
          className="size-full object-cover"
          // The lead image is what the page is for — do not defer it.
          loading="eager"
          decoding="async"
        />

        <button
          type="button"
          onClick={() => setExpanded(true)}
          aria-label={t("body.pgExpand")}
          className="absolute inset-0 flex cursor-zoom-in items-center justify-center bg-black/0 transition-colors hover:bg-black/15"
        >
          <Expand className="size-8 text-white opacity-0 drop-shadow transition-opacity group-hover:opacity-90" />
        </button>

        {count > 1 && (
          <>
            <GalleryArrow
              side="left"
              label={t("body.pgPrevious")}
              onClick={() => go(-1)}
            />
            <GalleryArrow
              side="right"
              label={t("body.pgNext")}
              onClick={() => go(1)}
            />
          </>
        )}

        <span className="tabular pointer-events-none absolute right-3 bottom-3 rounded-full bg-black/60 px-2.5 py-1 text-xs font-medium text-white">
          {t("body.pgPhotoOf", { n: index + 1, total: count })}
        </span>
      </div>

      {count > 1 && (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {photos.map((photo, position) => (
            <button
              key={photo.id}
              type="button"
              onClick={() => setIndex(position)}
              aria-label={t("body.pgPhotoOf", { n: position + 1, total: count })}
              aria-current={position === index}
              className={`relative size-16 shrink-0 overflow-hidden rounded-md border-2 transition-opacity ${
                position === index
                  ? "border-primary"
                  : "border-transparent opacity-65 hover:opacity-100"
              }`}
            >
              <img
                src={photo.url}
                alt=""
                loading="lazy"
                decoding="async"
                className="size-full object-cover"
              />
            </button>
          ))}
        </div>
      )}

      {expanded && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-4"
          role="dialog"
          aria-modal="true"
          aria-label={title}
          onClick={() => setExpanded(false)}
        >
          <img
            src={current.url}
            alt={t("body.pgPhotoOf", { n: index + 1, total: count })}
            className="max-h-full max-w-full object-contain"
            // The backdrop closes; a click on the picture itself should not.
            onClick={(event) => event.stopPropagation()}
          />

          <button
            type="button"
            onClick={() => setExpanded(false)}
            aria-label={t("buttons.close")}
            className="absolute top-4 right-4 rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          >
            <X className="size-5" />
          </button>

          {count > 1 && (
            <>
              <GalleryArrow
                side="left"
                label={t("body.pgPrevious")}
                onClick={() => go(-1)}
                stopPropagation
              />
              <GalleryArrow
                side="right"
                label={t("body.pgNext")}
                onClick={() => go(1)}
                stopPropagation
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function GalleryArrow({
  side,
  label,
  onClick,
  stopPropagation = false,
}: {
  side: "left" | "right";
  label: string;
  onClick: () => void;
  stopPropagation?: boolean;
}) {
  const Icon = side === "left" ? ChevronLeft : ChevronRight;
  return (
    <button
      type="button"
      aria-label={label}
      onClick={(event) => {
        if (stopPropagation) event.stopPropagation();
        onClick();
      }}
      className={`absolute top-1/2 -translate-y-1/2 rounded-full bg-black/45 p-2 text-white transition-colors hover:bg-black/70 ${
        side === "left" ? "left-3" : "right-3"
      }`}
    >
      <Icon className="size-5" />
    </button>
  );
}
