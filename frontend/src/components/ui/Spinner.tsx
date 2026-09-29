interface Props {
  size?: "sm" | "md";
  className?: string;
  label?: string | null;
}

/** A real "we are waiting on a genuine async operation" indicator — never
 * paired with a fabricated percentage; use ProgressBar instead when a real
 * numeric progress value exists. */
export function Spinner({ size = "sm", className = "", label = "Loading" }: Props) {
  const dimension = size === "sm" ? "h-3.5 w-3.5" : "h-5 w-5";
  return (
    <svg
      className={`animate-spin text-current ${dimension} ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      role={label ? "status" : undefined}
      aria-label={label ?? undefined}
      aria-hidden={label ? undefined : true}
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}
