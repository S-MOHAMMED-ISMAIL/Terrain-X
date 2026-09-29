import { useEffect, useRef, useState } from "react";

interface Props {
  /** The real, already-fetched value to reveal — never a placeholder or an
   * invented number. `null` means "still loading," rendered as "…". */
  value: number | null;
  className?: string;
}

/** Purely visual count-up reveal of a real number that has already arrived
 * from the backend — it never runs before the real value is known, and it
 * never displays anything other than that real value once the animation
 * settles. Jumps instantly under `prefers-reduced-motion: reduce`. */
export function AnimatedNumber({ value, className = "" }: Props) {
  const [displayed, setDisplayed] = useState<number | null>(value);
  const previousValue = useRef<number | null>(null);

  useEffect(() => {
    if (value === null) return;
    const from = previousValue.current ?? 0;
    const to = value;
    previousValue.current = value;

    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (prefersReducedMotion || from === to) {
      setDisplayed(to);
      return;
    }

    const durationMs = 500;
    const start = performance.now();
    let frame: number;

    function tick(now: number) {
      const elapsed = now - start;
      const progress = Math.min(1, elapsed / durationMs);
      const eased = 1 - (1 - progress) * (1 - progress);
      setDisplayed(Math.round(from + (to - from) * eased));
      if (progress < 1) {
        frame = requestAnimationFrame(tick);
      }
    }
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);

  return <span className={className}>{displayed === null ? "…" : displayed}</span>;
}
