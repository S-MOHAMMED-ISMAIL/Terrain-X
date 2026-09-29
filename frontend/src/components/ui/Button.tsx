import type { ButtonHTMLAttributes, ReactNode } from "react";
import { forwardRef } from "react";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "destructive" | "danger" | "ghost";
export type ButtonSize = "sm" | "md" | "lg" | "icon";

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    "border border-accent-active bg-accent-active text-slate-950 hover:border-accent hover:bg-accent active:bg-accent-active",
  secondary:
    "border border-slate-300 bg-white text-slate-800 hover:border-slate-400 hover:bg-slate-50 active:bg-slate-100",
  destructive:
    "border border-red-600 bg-red-600 text-white hover:border-red-700 hover:bg-red-700 active:bg-red-800",
  danger: "text-red-700 hover:bg-red-50 hover:text-red-800 active:bg-red-100",
  ghost: "text-slate-700 hover:bg-slate-100 hover:text-slate-950 active:bg-slate-200",
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
  sm: "min-h-8 px-2.5 py-1 text-xs",
  md: "min-h-control px-3 py-2 text-sm",
  lg: "min-h-touch px-4 py-2.5 text-sm",
  icon: "h-control w-control p-0",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  loadingLabel?: string;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "primary",
    size = "md",
    loading = false,
    loadingLabel,
    disabled,
    className = "",
    children,
    type = "button",
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`inline-flex shrink-0 items-center justify-center gap-2 rounded-control font-medium transition-colors duration-fast ease-standard focus-visible:ring-offset-white disabled:cursor-not-allowed disabled:opacity-45 ${SIZE_CLASSES[size]} ${VARIANT_CLASSES[variant]} ${className}`}
      {...rest}
    >
      {loading && <Spinner label={null} />}
      {loading ? (loadingLabel ?? children) : children}
    </button>
  );
});

interface IconButtonProps extends Omit<ButtonProps, "size" | "children"> {
  "aria-label": string;
  icon: ReactNode;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon, ...props },
  ref,
) {
  return (
    <Button ref={ref} size="icon" {...props}>
      <span aria-hidden="true">{icon}</span>
    </Button>
  );
});
