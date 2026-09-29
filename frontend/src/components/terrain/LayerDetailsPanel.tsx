import type { AnalysisJob, LayerContext } from "@/api/types";
import { DataState, Notice, TechnicalKeyValue } from "@/components/ui";
import { CategoricalLegend, FixedLegend, Legend } from "./Legend";
import {
  layerDataState,
  layerUnitLabel,
  noDataPresentation,
  type WorkspaceQualityPresentation,
} from "./workspacePresentation";

interface Props {
  layer: LayerContext | null;
  job: AnalysisJob | null;
  quality: WorkspaceQualityPresentation;
  metadataError?: string | null;
}

export function LayerDetailsPanel({ layer, job, quality, metadataError = null }: Props) {
  if (!layer) {
    return (
      <section aria-labelledby="layer-details-title" className="border-b border-slate-200 pb-3">
        <h3 id="layer-details-title" className="text-panel-title text-slate-950">Active layer</h3>
        <p className="mt-1 text-supporting text-slate-500">Select a reported layer to inspect its details.</p>
      </section>
    );
  }

  const state = layerDataState(layer, job);
  const unit = layerUnitLabel(layer, quality);

  return (
    <section aria-labelledby="layer-details-title" className="min-w-0 border-b border-slate-200 pb-3">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 id="layer-details-title" className="break-words text-panel-title text-slate-950">
            {layer.display_name}
          </h3>
          <p className="text-metadata text-slate-500">{unit}</p>
        </div>
        <DataState state={state.state} />
      </div>

      {state.detail && <p className="mt-2 break-words text-supporting text-slate-600">{state.detail}</p>}
      {metadataError && (
        <Notice tone="error" title="Layer metadata unavailable" className="mt-2">
          {metadataError}
        </Notice>
      )}

      {layer.available && (
        <div className="mt-3 min-w-0 space-y-3">
          {layer.layer_type !== "rgb" && (
            layer.is_categorical ? (
              layer.legend ? <FixedLegend entries={layer.legend} /> : <CategoricalLegend regionCount={layer.region_count} />
            ) : (
              <Legend
                minValue={layer.min_value}
                maxValue={layer.max_value}
                unitLabel={layer.layer_type === "slope" || layer.layer_type === "aspect" ? "degrees" : unit}
              />
            )
          )}

          <dl className="space-y-1.5">
            <TechnicalKeyValue label="Dimensions" value={
              layer.width && layer.height ? `${layer.width} x ${layer.height} px` : "Not reported"
            } />
            <TechnicalKeyValue label="CRS" value={
              layer.crs ?? (layer.is_georeferenced ? "Unknown" : "Local pixel coordinates")
            } />
            <TechnicalKeyValue label="Data type" value={layer.dtype ?? "Not reported"} />
            <TechnicalKeyValue label="NoData" value={noDataPresentation(layer)} />
          </dl>

          {layer.notes && <p className="break-words text-metadata leading-4 text-slate-600">{layer.notes}</p>}
          <p className="text-metadata leading-4 text-slate-500">
            Legend colors visualize the backend-reported range or classes; they are not independent measurements.
          </p>
        </div>
      )}
    </section>
  );
}
