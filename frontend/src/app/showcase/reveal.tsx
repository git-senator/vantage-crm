"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import styles from "./showcase.module.css";

/**
 * Fades a section in the first time it is scrolled to.
 *
 * `IntersectionObserver` rather than a scroll listener: the observer fires off
 * the main thread and once per element, where a scroll handler runs on every
 * frame of every scroll for the whole page. On a page carrying several hundred
 * photographs that difference is the difference between smooth and not.
 *
 * It unobserves after the first crossing — a section that fades out when you
 * scroll back up looks broken, not elegant.
 */
export function Reveal({
  children,
  delay = 0,
}: {
  children: ReactNode;
  delay?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    // No observer (or a very old browser): show the content rather than hide
    // it forever. A progressive enhancement that can hide the page is a bug.
    if (typeof IntersectionObserver === "undefined") {
      setShown(true);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setShown(true);
            observer.unobserve(entry.target);
          }
        }
      },
      // Start the fade slightly before the section reaches the fold, so it has
      // finished by the time the reader is actually looking at it.
      { rootMargin: "0px 0px -12% 0px", threshold: 0.05 },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={`${styles.reveal} ${shown ? styles.revealed : ""}`}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  );
}
