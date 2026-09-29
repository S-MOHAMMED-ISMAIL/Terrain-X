interface Props {
  /** 0-100. Always a real value already computed by the backend (e.g. a
   * job's own `progress` field, derived server-side from its real stage) —
   * this component never invents or estimates a percentage itself. */
  value: number;
  tone?: "brand" | "success" | "danger" | "neutral";
  className?: string;
}

const TONE_CLASSES: Record<NonNullable<Props["tone"]>, string> = {
  brand: "bg-brand-500",
  success: "bg-green-500",
  danger: "bg-red-500",
  neutral: "bg-slate-400",
};

export function ProgressBar({ value, tone = "brand", className = "" }: Props) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div
      className={`h-1.5 w-full overflow-hidden rounded-full bg-slate-100 ${className}`}
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={`h-full rounded-full transition-[width] duration-500 ease-out ${TONE_CLASSES[tone]}`}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
