import {
  forwardRef,
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

const CONTROL_BASE =
  "min-h-control w-full min-w-0 rounded-control border border-slate-300 bg-white px-3 py-2 text-control text-slate-900 transition-colors duration-fast placeholder:text-slate-500 hover:border-slate-400 focus:border-accent-active disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500 disabled:opacity-70 aria-[invalid=true]:border-red-600 aria-[invalid=true]:ring-1 aria-[invalid=true]:ring-red-200";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className = "", ...props }, ref) {
    return <input ref={ref} className={`${CONTROL_BASE} ${className}`} {...props} />;
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className = "", ...props }, ref) {
    return <select ref={ref} className={`${CONTROL_BASE} ${className}`} {...props} />;
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className = "", ...props }, ref) {
    return <textarea ref={ref} className={`${CONTROL_BASE} min-h-24 resize-y ${className}`} {...props} />;
  },
);

const CHECK_BASE =
  "h-4 w-4 shrink-0 border-slate-300 text-accent-active accent-accent-active disabled:cursor-not-allowed disabled:opacity-50";

export const Checkbox = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Checkbox({ className = "", type = "checkbox", ...props }, ref) {
    return <input ref={ref} type={type} className={`${CHECK_BASE} rounded-sm ${className}`} {...props} />;
  },
);

export const Radio = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Radio({ className = "", type = "radio", ...props }, ref) {
    return <input ref={ref} type={type} className={`${CHECK_BASE} ${className}`} {...props} />;
  },
);

export interface FieldControlProps {
  id: string;
  required?: boolean;
  "aria-invalid"?: true;
  "aria-describedby"?: string;
}

interface FieldProps {
  id?: string;
  label: ReactNode;
  description?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  className?: string;
  children: (props: FieldControlProps) => ReactNode;
}

export function Field({
  id: providedId,
  label,
  description,
  error,
  required = false,
  className = "",
  children,
}: FieldProps) {
  const generatedId = useId();
  const id = providedId ?? `field-${generatedId.replaceAll(":", "")}`;
  const descriptionId = description ? `${id}-description` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={`min-w-0 ${className}`}>
      <label htmlFor={id} className="mb-1 block text-control font-medium text-slate-700">
        {label}
        {required && (
          <span className="ml-1 text-red-700" aria-hidden="true">
            *
          </span>
        )}
        {required && <span className="sr-only"> (required)</span>}
      </label>
      {children({
        id,
        required: required || undefined,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": describedBy,
      })}
      {description && (
        <p id={descriptionId} className="mt-1 text-supporting text-slate-500">
          {description}
        </p>
      )}
      {error && (
        <p id={errorId} className="mt-1 text-supporting font-medium text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}

interface SwitchProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "role" | "onChange"> {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}

export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(function Switch(
  { checked, onCheckedChange, disabled, className = "", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={`inline-flex h-control w-touch items-center rounded-full p-1 transition-colors duration-selection focus-visible:ring-offset-white disabled:cursor-not-allowed disabled:opacity-50 ${checked ? "bg-accent-active" : "bg-slate-300"} ${className}`}
      {...props}
    >
      <span
        aria-hidden="true"
        className={`h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-selection ${checked ? "translate-x-4" : "translate-x-0"}`}
      />
    </button>
  );
});
