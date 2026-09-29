import {
  Children,
  cloneElement,
  useId,
  useState,
  type FocusEvent,
  type KeyboardEvent,
  type MouseEvent,
  type ReactElement,
} from "react";

interface TooltipProps {
  content: string;
  children: ReactElement;
  className?: string;
}

export function Tooltip({ content, children, className = "" }: TooltipProps) {
  const id = `tooltip-${useId().replaceAll(":", "")}`;
  const [open, setOpen] = useState(false);
  const child = Children.only(children) as ReactElement<Record<string, unknown>>;
  const props = child.props as {
    "aria-describedby"?: string;
    onFocus?: (event: FocusEvent) => void;
    onBlur?: (event: FocusEvent) => void;
    onMouseEnter?: (event: MouseEvent) => void;
    onMouseLeave?: (event: MouseEvent) => void;
    onKeyDown?: (event: KeyboardEvent) => void;
  };
  const describedBy = [props["aria-describedby"], id].filter(Boolean).join(" ");

  return (
    <span className={`relative inline-flex ${className}`}>
      {cloneElement(child, {
        "aria-describedby": describedBy,
        onFocus: (event: FocusEvent) => {
          props.onFocus?.(event);
          setOpen(true);
        },
        onBlur: (event: FocusEvent) => {
          props.onBlur?.(event);
          setOpen(false);
        },
        onMouseEnter: (event: MouseEvent) => {
          props.onMouseEnter?.(event);
          setOpen(true);
        },
        onMouseLeave: (event: MouseEvent) => {
          props.onMouseLeave?.(event);
          setOpen(false);
        },
        onKeyDown: (event: KeyboardEvent) => {
          props.onKeyDown?.(event);
          if (event.key === "Escape") setOpen(false);
        },
      })}
      <span
        id={id}
        role="tooltip"
        hidden={!open}
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-max max-w-64 -translate-x-1/2 rounded-control bg-surface-raised px-2 py-1 text-metadata text-content-primary shadow-popover"
      >
        {content}
      </span>
    </span>
  );
}
