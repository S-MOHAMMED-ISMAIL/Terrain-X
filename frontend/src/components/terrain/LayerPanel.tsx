import type { AnalysisJob, LayerContext, LayerType } from "@/api/types";
import { DataState } from "@/components/ui";
import { jobForLayer, layerDataState, layerUnitLabel, type WorkspaceQualityPresentation } from "./workspacePresentation";

interface Props {
  layers: LayerContext[];
  activeLayer: LayerType;
  onActiveLayerChange: (layer: LayerType) => void;
  visibility: Record<LayerType, boolean>;
  onToggleVisibility: (layer: LayerType) => void;
  opacity: Record<LayerType, number>;
  onOpacityChange: (layer: LayerType, value: number) => void;
  job: AnalysisJob | null;
  jobs?: AnalysisJob[];
  quality: WorkspaceQualityPresentation;
}

/** Generated strictly from the real `layers` the backend actually reported
 * (see VisualizationContext) — a layer that doesn't exist yet (e.g. metric
 * elevation before calibration succeeds) is rendered as a real, explained
 * "unavailable" row, never a fake enabled control. */
export function LayerPanel({
  layers,
  activeLayer,
  onActiveLayerChange,
  visibility,
  onToggleVisibility,
  opacity,
  onOpacityChange,
  job,
  jobs = [],
  quality,
}: Props) {
  return (
    <div className="flex flex-col gap-1" role="list" aria-label="Terrain layers">
      {layers.map((layer) => {
        const isActive = layer.layer_type === activeLayer;
        const dataState = layerDataState(layer, jobForLayer(layer, jobs, job));
        const descriptionId = `layer-${layer.layer_type}-description`;
        const stateClass = !layer.available
          ? "layer-state-unavailable"
          : dataState.state === "processing"
            ? "layer-state-processing"
            : dataState.state === "failed"
              ? "layer-state-failed"
              : layer.layer_type === "relative_depth"
                ? "layer-state-relative"
                : ["metric_elevation", "dsm", "dtm", "ndsm"].includes(layer.layer_type)
                  ? "layer-state-metric"
                  : "layer-state-available";
        return (
          <div
            key={layer.layer_type}
            role="listitem"
            data-active={isActive ? "true" : "false"}
            data-state={dataState.state}
            className={`rounded-panel border px-2 py-1.5 transition-colors duration-selection ${stateClass} ${
              isActive
                ? "border-accent-active bg-brand-50"
                : "border-transparent bg-white hover:border-slate-200"
            } ${layer.available ? "" : "opacity-60"}`}
          >
            <div className="flex items-start justify-between gap-2">
              <button
                type="button"
                disabled={!layer.available}
                aria-pressed={isActive}
                aria-label={layer.display_name}
                aria-describedby={descriptionId}
                onClick={() => onActiveLayerChange(layer.layer_type)}
                className={`min-h-control min-w-0 flex-1 text-left text-control font-medium ${
                  layer.available ? "text-slate-900" : "text-slate-500"
                } disabled:cursor-not-allowed`}
              >
                <span className="block break-words">{layer.display_name}</span>
                <span id={descriptionId} className="block text-metadata font-normal text-slate-500">
                  {layerUnitLabel(layer, quality)}
                </span>
              </button>
              {layer.available && (
                <label className="flex min-h-control shrink-0 items-center gap-1.5 text-metadata text-slate-600">
                  <input
                    type="checkbox"
                    aria-label="Visible"
                    checked={visibility[layer.layer_type]}
                    onChange={() => onToggleVisibility(layer.layer_type)}
                    className="accent-accent-active"
                  />
                  <span>Visible</span>
                </label>
              )}
            </div>

            <div className="mt-1">
              <DataState state={dataState.state} />
            </div>

            {!layer.available && dataState.detail && (
              <p className="mt-1 break-words text-metadata leading-4 text-slate-500">{dataState.detail}</p>
            )}

            {layer.available && isActive && (
              <div className="mt-1.5 border-t border-slate-200 pt-2">
                <label className="flex items-center gap-2 text-metadata text-slate-600">
                  Opacity
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={Math.round(opacity[layer.layer_type] * 100)}
                    onChange={(e) => onOpacityChange(layer.layer_type, Number(e.target.value) / 100)}
                    className="min-w-0 flex-1 accent-accent-active"
                  />
                  <span className="w-8 text-right">
                    {Math.round(opacity[layer.layer_type] * 100)}%
                  </span>
                </label>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
