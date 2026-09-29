import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

export interface TabItem {
  id: string;
  label: ReactNode;
  panel: ReactNode;
  disabled?: boolean;
}

interface TabsProps {
  items: TabItem[];
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  ariaLabel: string;
  className?: string;
}

export function Tabs({
  items,
  value,
  defaultValue,
  onValueChange,
  ariaLabel,
  className = "",
}: TabsProps) {
  const instanceId = useId().replaceAll(":", "");
  const firstEnabled = items.find((item) => !item.disabled)?.id ?? "";
  const [internalValue, setInternalValue] = useState(defaultValue ?? firstEnabled);
  const selected = value ?? internalValue;
  const refs = useRef(new Map<string, HTMLButtonElement>());

  function choose(id: string) {
    if (value === undefined) setInternalValue(id);
    onValueChange?.(id);
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, currentId: string) {
    const enabled = items.filter((item) => !item.disabled);
    const index = enabled.findIndex((item) => item.id === currentId);
    if (index < 0) return;

    let nextIndex: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = (index + 1) % enabled.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (index - 1 + enabled.length) % enabled.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = enabled.length - 1;
    }
    if (nextIndex === null) return;
    event.preventDefault();
    const nextId = enabled[nextIndex].id;
    choose(nextId);
    refs.current.get(nextId)?.focus();
  }

  const selectedItem = items.find((item) => item.id === selected && !item.disabled) ?? items.find((item) => !item.disabled);

  return (
    <div className={`min-w-0 ${className}`}>
      <div role="tablist" aria-label={ariaLabel} className="flex min-w-0 flex-wrap gap-1 border-b border-slate-200">
        {items.map((item) => {
          const active = item.id === selectedItem?.id;
          const tabId = `${instanceId}-tab-${item.id}`;
          const panelId = `${instanceId}-panel-${item.id}`;
          return (
            <button
              key={item.id}
              ref={(node) => {
                if (node) refs.current.set(item.id, node);
                else refs.current.delete(item.id);
              }}
              id={tabId}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={panelId}
              tabIndex={active ? 0 : -1}
              disabled={item.disabled}
              onClick={() => choose(item.id)}
              onKeyDown={(event) => onKeyDown(event, item.id)}
              className={`min-h-control border-b-2 px-3 py-2 text-control font-medium transition-colors duration-selection disabled:cursor-not-allowed disabled:opacity-45 ${active ? "border-accent-active text-slate-950" : "border-transparent text-slate-600 hover:text-slate-950"}`}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      {selectedItem && (
        <div
          id={`${instanceId}-panel-${selectedItem.id}`}
          role="tabpanel"
          aria-labelledby={`${instanceId}-tab-${selectedItem.id}`}
          tabIndex={0}
          className="min-w-0 py-3"
        >
          {selectedItem.panel}
        </div>
      )}
    </div>
  );
}
