import type {
  LayerContext,
  LayerType,
  LegendEntry,
  PixelValue,
  SemanticRegion,
  VisualizationContext,
} from "@/api/types";

// Topmost-rendered-first — mirrors MapView2D's own stacking order (rgb,
// relative_depth, metric_elevation, dsm, dtm, ndsm, semantic_segmentation, slope,
// aspect, hillshade, flood_screening, landslide_screening added to the map
// in that sequence, so landslide_screening ends up drawn on top), reversed
// and with "rgb" excluded since it has no single scalar value to sample. Used so
// cursor inspection reflects whichever scientific layer is actually visible
// on top, not an "active" selection the user has no reason to know exists
// separately from the visibility checkbox they actually used.
export const LAYER_INSPECTION_PRIORITY: LayerType[] = [
  "landslide_screening",
  "flood_screening",
  "hillshade",
  "aspect",
  "slope",
  "semantic_segmentation",
  "ndsm",
  "dtm",
  "dsm",
  "metric_elevation",
  "relative_depth",
];

/**
 * Picks which layer a map/terrain click should sample a real value from.
 * Prefers the user's explicitly-selected `activeLayer` when it is itself a
 * visible, available scientific layer; otherwise falls back to the topmost
 * visible scientific layer so a click samples whatever the user can
 * actually see, without requiring them to separately click a layer's name
 * in the panel (a real, reported bug: toggling visibility alone left
 * `activeLayer` at its "rgb" default forever, so no /value request was ever
 * made). Returns null when no scientific layer is currently visible — in
 * that case there is genuinely nothing to sample, not a bug to hide.
 *
 * Kept in its own module (no React/Leaflet/Three imports) specifically so
 * it can be unit-tested directly — see TerrainWorkspace.test.ts — without
 * pulling in browser-only libraries that this project's Vitest setup has no
 * jsdom environment configured for.
 */
export function pickInspectableLayer(
  context: VisualizationContext,
  activeLayer: LayerType,
  visibility: Record<LayerType, boolean>,
): LayerContext | null {
  const isInspectable = (layer: LayerContext | undefined): layer is LayerContext =>
    !!layer && layer.available && layer.layer_type !== "rgb" && visibility[layer.layer_type];

  const active = context.layers.find((l) => l.layer_type === activeLayer);
  if (isInspectable(active)) return active;

  for (const layerType of LAYER_INSPECTION_PRIORITY) {
    const layer = context.layers.find((l) => l.layer_type === layerType);
    if (isInspectable(layer)) return layer;
  }
  return null;
}

// A real, valid, perfectly-flat pixel (slope==0) has an undefined compass
// direction — the backend reports this as -1 rather than NoData (see
// geospatial/terrain_derivatives.py::ASPECT_FLAT_SENTINEL), so it must be
// displayed as "flat", never as a bogus 359°-ish bearing.
const ASPECT_FLAT_SENTINEL = -1;

/**
 * Formats one sampled pixel value for display. `legend` is required for
 * flood_screening/landslide_screening (a fixed, real value->label mapping
 * persisted by the backend — see LayerContext.legend) since their raw
 * integer class codes carry no meaning on their own; every other layer type
 * ignores it.
 */
export function formatSample(
  sample: PixelValue,
  layerType: LayerType,
  legend?: LegendEntry[] | null,
): string {
  if (sample.value === null) return "No data";
  if (layerType === "semantic_segmentation") {
    // A real region ID from the categorical raster — never a semantic class
    // name (see docs/ARCHITECTURE.md §3.6). 0 is the reserved
    // background/NoData value, not "region 0".
    return sample.value === 0 ? "Background / no region" : `Region ID: ${sample.value}`;
  }
  if (layerType === "flood_screening" || layerType === "landslide_screening") {
    const displayLabel = layerType === "flood_screening" ? "Flood screening" : "Landslide screening";
    const entry = legend?.find((e) => e.value === sample.value);
    return `${displayLabel}: ${entry ? entry.label : `Class ${sample.value}`}`;
  }
  if (layerType === "slope") {
    return `Slope: ${sample.value.toFixed(1)}°`;
  }
  if (layerType === "aspect") {
    return sample.value === ASPECT_FLAT_SENTINEL
      ? "Aspect: Flat terrain (no defined direction)"
      : `Aspect: ${sample.value.toFixed(1)}° (compass bearing)`;
  }
  if (layerType === "hillshade") {
    // Real illumination fraction (0.0-1.0, see
    // geospatial/terrain_derivatives.py::compute_hillshade) — never a
    // height value, shown as a percentage for readability.
    return `Hillshade: ${(sample.value * 100).toFixed(0)}% illuminated`;
  }
  // P1-3: raster-derived ESTIMATES — labelled so they are never read as a
  // measured bare-earth elevation or a measured object height.
  if (layerType === "dtm") {
    return `Estimated bare-earth elevation: ${sample.value.toFixed(3)}`;
  }
  if (layerType === "ndsm") {
    return `Estimated height above ground: ${sample.value.toFixed(3)}`;
  }
  const label =
    layerType === "relative_depth"
      ? "Relative inverse depth"
      : layerType === "metric_elevation" || layerType === "dsm"
        ? "Elevation"
        : "Value";
  return `${label}: ${sample.value.toFixed(3)}`;
}

/**
 * Looks up the real, already-persisted region metadata (pixel area, model
 * mask score) for the region ID a semantic_segmentation sample returned —
 * so inspection can show more than a bare ID without re-deriving anything.
 * Returns null for background (id 0) or an ID with no matching entry.
 */
export function findSemanticRegion(
  regions: SemanticRegion[] | null | undefined,
  regionId: number,
): SemanticRegion | null {
  if (!regions || regionId === 0) return null;
  return regions.find((r) => r.region_id === regionId) ?? null;
}
