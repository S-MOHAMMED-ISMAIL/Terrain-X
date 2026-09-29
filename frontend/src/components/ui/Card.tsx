import type { HTMLAttributes } from "react";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  interactive?: boolean;
  padding?: "none" | "sm" | "md";
}

/** The one card container used throughout TERRAIN-X — a plain, real
 * presentational wrapper (no data logic of its own) so every panel in the
 * app shares the same border/radius/shadow language instead of each page
 * hand-rolling its own `rounded-lg border ...` string. */
export function Card({
  className = "",
  interactive = false,
  padding = "md",
  children,
  ...rest
}: CardProps) {
  const paddingClass = padding === "none" ? "" : padding === "sm" ? "p-3" : "p-4";
  return (
    <div
      className={`rounded-lg border border-slate-200 bg-white shadow-panel ${paddingClass} ${
        interactive
          ? "transition-all duration-150 hover:-translate-y-0.5 hover:shadow-elevated"
          : ""
      } ${className}`}
      {...rest}
    >
      {children}
    </div>
  );
}
