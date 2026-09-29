import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui";

export type WorkspaceMode = "explore" | "measure" | "screen" | "flythrough";

export interface QualityItem {
  label: string;
  value: string;
  tone?: "neutral" | "accent" | "warning";
  technical?: boolean;
}

interface Props {
  projectName: string;
  datasetName: string;
  datasetControl: ReactNode;
  viewControl: ReactNode;
  mode: WorkspaceMode;
  onModeChange: (mode: WorkspaceMode) => void;
  qualityItems: QualityItem[];
  qualityDetails?: ReactNode;
  layers: ReactNode;
  viewport: ReactNode;
  inspector: ReactNode;
  drawer: ReactNode;
}

const MODE_LABELS: Record<WorkspaceMode, string> = {
  explore: "Explore",
  measure: "Measure",
  screen: "Screen",
  flythrough: "Flythrough",
};

export function TerrainWorkspaceShell({
  projectName,
  datasetName,
  datasetControl,
  viewControl,
  mode,
  onModeChange,
  qualityItems,
  qualityDetails,
  layers,
  viewport,
  inspector,
  drawer,
}: Props) {
  const id = useId().replaceAll(":", "");
  const layersId = `${id}-layers`;
  const inspectorId = `${id}-inspector`;
  const drawerId = `${id}-drawer`;
  const persistentRails =
    typeof window === "undefined" || !window.matchMedia
      ? true
      : window.matchMedia("(min-width: 1280px)").matches;
  const [layersOpen, setLayersOpen] = useState(persistentRails);
  const [inspectorOpen, setInspectorOpen] = useState(persistentRails);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const inspectorRef = useRef<HTMLElement>(null);

  useEffect(() => {
    inspectorRef.current?.scrollTo?.({ top: 0 });
  }, [mode]);

  useEffect(() => {
    if (!window.matchMedia) return;
    const query = window.matchMedia("(min-width: 1280px)");
    const syncRails = (event: MediaQueryListEvent) => {
      setLayersOpen(event.matches);
      setInspectorOpen(event.matches);
    };
    query.addEventListener("change", syncRails);
    return () => query.removeEventListener("change", syncRails);
  }, []);

  return (
    <section
      aria-label="Terrain workspace"
      className="terrain-workspace-shell overflow-hidden border border-ui-border-subtle bg-surface-app text-content-primary shadow-elevated"
      data-testid="terrain-workspace-shell"
    >
      <header className="border-b border-ui-border-subtle bg-surface-chrome">
        <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
          <div className="min-w-0 flex-[1_1_100%] sm:flex-1">
            <h1 className="truncate text-workspace-title text-content-primary">{projectName}</h1>
            <p className="truncate text-supporting text-content-secondary">
              {datasetName || "Select a dataset to begin"}
            </p>
          </div>
          <div className="min-w-0 flex-[1_1_100%] sm:max-w-sm sm:flex-1">{datasetControl}</div>
          <div className="ui-control-row w-full shrink-0 sm:w-auto">{viewControl}</div>
        </div>

        <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-t border-ui-border-subtle px-3 py-1.5">
          <div className="flex min-w-0 flex-wrap gap-1" role="group" aria-label="Workspace mode">
            {(Object.keys(MODE_LABELS) as WorkspaceMode[]).map((item) => (
              <button
                key={item}
                type="button"
                aria-pressed={mode === item}
                onClick={() => {
                  setInspectorOpen(true);
                  onModeChange(item);
                }}
                className={`min-h-control rounded-control px-3 text-control font-medium transition-colors duration-selection ${
                  mode === item
                    ? "bg-accent text-slate-950"
                    : "text-content-secondary hover:bg-surface-raised hover:text-content-primary"
                }`}
              >
                {MODE_LABELS[item]}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-1" aria-label="Workspace panels">
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={layersOpen}
              aria-controls={layersId}
              onClick={() => setLayersOpen((open) => !open)}
              className="text-content-secondary hover:bg-surface-raised hover:text-content-primary"
            >
              {layersOpen ? "Hide layers" : "Show layers"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={inspectorOpen}
              aria-controls={inspectorId}
              onClick={() => setInspectorOpen((open) => !open)}
              className="text-content-secondary hover:bg-surface-raised hover:text-content-primary"
            >
              {inspectorOpen ? "Hide tools" : "Show tools"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={drawerOpen}
              aria-controls={drawerId}
              onClick={() => setDrawerOpen((open) => !open)}
              className="text-content-secondary hover:bg-surface-raised hover:text-content-primary"
            >
              {drawerOpen ? "Hide results" : "Show results"}
            </Button>
          </div>
        </div>
      </header>

      <section aria-label="Data quality" className="border-b border-ui-border-subtle bg-surface-panel px-3 py-2">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <dl className="flex min-w-0 flex-wrap items-center gap-2">
          {qualityItems.map((item) => (
            <div key={item.label} className="status-bar-item">
              <dt className="status-bar-label">{item.label}</dt>
              <dd
                className={`status-bar-value ${item.tone === "warning" ? "warning" : item.tone === "accent" ? "success" : ""}`}
              >
                {item.value}
              </dd>
            </div>
          ))}
        </dl>
          {qualityDetails && (
            <details className="group relative shrink-0 text-supporting">
              <summary className="h-6 cursor-pointer content-center text-metadata font-medium text-accent-hover focus-visible:outline-none">
                Quality details
              </summary>
              <div className="absolute right-0 top-full z-[1200] mt-2 max-h-64 w-[min(32rem,calc(100vw-2rem))] min-w-0 overflow-auto rounded-panel border border-ui-border-strong bg-surface-panel p-3 text-content-secondary shadow-popover">
                {qualityDetails}
              </div>
            </details>
          )}
        </div>
      </section>

      <div
        className="terrain-workspace-body"
        data-layers-open={layersOpen}
        data-inspector-open={inspectorOpen}
      >
        <aside
          id={layersId}
          aria-label="Layers"
          aria-hidden={!layersOpen}
          {...(!layersOpen ? { inert: "" } : {})}
          className="terrain-layer-rail min-h-0 overflow-y-auto border-ui-border-subtle bg-surface-data text-slate-900"
        >
          <div className="sticky top-0 z-10 border-b border-slate-200 bg-surface-data px-3 py-2">
            <h2 className="text-panel-title text-slate-950">Layers</h2>
          </div>
          <div className="p-2">{layers}</div>
        </aside>

        <main className="terrain-viewport min-h-0 min-w-0 overflow-hidden bg-surface-canvas" aria-label="Terrain viewport">
          {viewport}
        </main>

        <aside
          ref={inspectorRef}
          id={inspectorId}
          aria-label="Inspector and tools"
          aria-hidden={!inspectorOpen}
          {...(!inspectorOpen ? { inert: "" } : {})}
          className="terrain-inspector-rail min-h-0 overflow-y-auto border-ui-border-subtle bg-surface-data text-slate-900"
        >
          <div className="sticky top-0 z-10 border-b border-slate-200 bg-surface-data px-3 py-2">
            <h2 className="text-panel-title text-slate-950">{MODE_LABELS[mode]} tools</h2>
          </div>
          <div className="flex min-w-0 flex-col gap-3 p-3">{inspector}</div>
        </aside>
      </div>

      <section
        id={drawerId}
        aria-label="Workspace results"
        aria-hidden={!drawerOpen}
        className={`terrain-workspace-drawer border-t border-ui-border-subtle bg-surface-data text-slate-900 ${
          drawerOpen ? "is-open" : ""
        }`}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
          <h2 className="text-panel-title text-slate-950">Results and diagnostics</h2>
          <span className="text-metadata text-slate-500">Viewport remains active</span>
        </div>
        <div
          {...(!drawerOpen ? { inert: "" } : {})}
          className="terrain-workspace-drawer-content min-w-0 overflow-auto p-3"
        >
          {drawer}
        </div>
      </section>
    </section>
  );
}
