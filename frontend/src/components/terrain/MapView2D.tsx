import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import type { LayerType, ResidualFeature, ResidualKind, VisualizationContext } from "@/api/types";
import { residualColor, residualTooltipLines, residualValue, symmetricScaleMax } from "./calibrationResiduals";
import { type MapClick, mapClickFromLatLng, overlayCorners } from "./mapClick";

const TILE_URL =
  import.meta.env.VITE_MAP_TILE_URL ?? "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  import.meta.env.VITE_MAP_TILE_ATTRIBUTION ?? "&copy; OpenStreetMap contributors";

// Fixed stacking order (bottom to top) — matches the LayerPanel's own order,
// so what's "on top" in the panel is also on top on the map. semantic_segmentation
// is drawn above the depth-family layers since it's an optional overlay/context
// layer, never a height source (see docs/ARCHITECTURE.md §3.6). Phase 8's
// terrain-derivative/hazard layers are drawn topmost since they're the most
// recently-requested analysis a user runs on top of an existing dataset.
const LAYER_ORDER: LayerType[] = [
  "rgb",
  "relative_depth",
  "metric_elevation",
  "dsm",
  "dtm",
  "ndsm",
  "semantic_segmentation",
  "slope",
  "aspect",
  "hillshade",
  "flood_screening",
  "landslide_screening",
];

// P1-5: calibration residual POINTS (never a raster/surface), drawn in their
// own pane above the raster overlays.
export interface ResidualOverlay {
  features: ResidualFeature[];
  kind: ResidualKind;
  referenceType: "dem" | "gcp";
  // False in any measurement mode, so markers never swallow a map click.
  interactive: boolean;
}

const RESIDUAL_PANE = "calibrationResiduals";

interface Props {
  projectId: string;
  context: VisualizationContext;
  visibility: Record<LayerType, boolean>;
  opacity: Record<LayerType, number>;
  // Georeferenced: the clicked map coordinate only (resolved to a source
  // pixel by the backend, per layer). Non-georeferenced: the local pixel.
  onSample: (sample: MapClick | null) => void;
  residuals?: ResidualOverlay | null;
}

/** A real Leaflet 2D viewer. Georeferenced datasets are placed on a standard
 * lat/lon web basemap using bounds actually reprojected to EPSG:4326 by the
 * backend (rasterio/GDAL/PROJ — never an ad-hoc conversion here); a
 * non-georeferenced dataset instead uses Leaflet's CRS.Simple with plain
 * pixel coordinates and no basemap — never a fabricated geographic
 * location. See docs/ARCHITECTURE.md §3.5. */
export function MapView2D({ projectId, context, visibility, opacity, onSample, residuals }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const residualLayerRef = useRef<L.LayerGroup | null>(null);
  // P1-6: a layer that could not be placed is reported, never drawn over
  // some other box.
  const [overlayErrors, setOverlayErrors] = useState<Partial<Record<LayerType, string>>>({});
  const mapRef = useRef<L.Map | null>(null);
  const overlaysRef = useRef<Partial<Record<LayerType, L.ImageOverlay>>>({});
  const objectUrlsRef = useRef<Partial<Record<LayerType, string>>>({});
  const isGeoreferenced = context.dataset.is_georeferenced && context.dataset.bounds_wgs84 !== null;

  // Real, always-current mirror of the `onSample` prop (which changes
  // identity on every TerrainWorkspace render, e.g. whenever measurement
  // mode changes) — read by the map's "click" listener below instead of
  // closing over `onSample` directly. The listener itself is registered
  // once per dataset (see the effect's dependency array further down,
  // intentionally NOT including `onSample`, to avoid tearing down and
  // rebuilding the whole Leaflet map on every unrelated parent re-render);
  // without this ref, that one-time closure would keep calling whatever
  // `onSample`/measurement-mode handler existed at mount forever, silently
  // ignoring any later mode switch. Mirrors the identical, already-
  // established `onExitFirstPersonRef` pattern in TerrainView3D.tsx.
  const onSampleRef = useRef(onSample);
  onSampleRef.current = onSample;

  // Create the map once per dataset (re-keyed by dataset id + georeferencing
  // mode, since CRS can't be changed on an existing Leaflet map instance).
  useEffect(() => {
    if (!containerRef.current) return;

    const map = L.map(containerRef.current, {
      crs: isGeoreferenced ? L.CRS.EPSG3857 : L.CRS.Simple,
      minZoom: isGeoreferenced ? undefined : -4,
      attributionControl: isGeoreferenced,
    });
    mapRef.current = map;
    setOverlayErrors({});
    const resizeObserver = new ResizeObserver(() => map.invalidateSize({ pan: false }));
    resizeObserver.observe(containerRef.current);
    // Above the raster image overlays (overlayPane, z 400), below popups.
    map.createPane(RESIDUAL_PANE).style.zIndex = "450";

    if (isGeoreferenced && context.dataset.bounds_wgs84) {
      L.tileLayer(TILE_URL, { attribution: TILE_ATTRIBUTION, maxZoom: 19 }).addTo(map);
      const b = context.dataset.bounds_wgs84;
      map.fitBounds([
        [b.min_y, b.min_x],
        [b.max_y, b.max_x],
      ]);
    } else if (context.dataset.width && context.dataset.height) {
      const bounds: L.LatLngBoundsExpression = [
        [0, 0],
        [context.dataset.height, context.dataset.width],
      ];
      map.fitBounds(bounds);
    } else {
      map.setView([0, 0], 0);
    }

    // P1-6: a georeferenced click is passed on as its real map coordinate
    // only — the backend resolves it against each sampled layer's own
    // raster. No row/col is ever derived from it here.
    map.on("click", (event: L.LeafletMouseEvent) => {
      const { lat, lng } = event.latlng;
      onSampleRef.current(mapClickFromLatLng(lat, lng, isGeoreferenced));
    });

    return () => {
      resizeObserver.disconnect();
      map.remove();
      mapRef.current = null;
      residualLayerRef.current = null;
      overlaysRef.current = {};
      const urls = objectUrlsRef.current;
      Object.values(urls).forEach((url) => url && URL.revokeObjectURL(url));
      objectUrlsRef.current = {};
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [context.dataset.id, isGeoreferenced]);

  // Add/remove/re-opacity overlays whenever visibility or opacity changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    let cancelled = false;

    async function sync() {
      for (const layerType of LAYER_ORDER) {
        const layer = context.layers.find((l) => l.layer_type === layerType);
        if (!layer || !layer.available) continue;
        const shouldShow = visibility[layerType];

        if (!shouldShow) {
          const existing = overlaysRef.current[layerType];
          if (existing && map!.hasLayer(existing)) map!.removeLayer(existing);
          continue;
        }

        let overlay = overlaysRef.current[layerType];
        if (!overlay) {
          // P1-6: georeferenced layers use their own EPSG:3857 overlay and
          // its exact corners; local-pixel (CRS.Simple) layers are unchanged.
          const bounds = overlayCorners(layer, isGeoreferenced, context.dataset);
          if (!bounds) {
            setOverlayErrors((prev) => ({
              ...prev,
              [layerType]: `${layer.display_name} could not be placed on the map.`,
            }));
            continue;
          }
          let blob: Blob;
          try {
            if (layerType === "rgb") {
              blob = isGeoreferenced
                ? await api.getDatasetMapPreviewBlob(projectId, context.dataset.id)
                : await api.getDatasetPreviewBlob(projectId, context.dataset.id);
            } else {
              const jobId = layer.analysis_job_id!;
              const artifactId = layer.artifact_id!;
              blob = isGeoreferenced
                ? await api.getArtifactMapPreviewBlob(projectId, jobId, artifactId)
                : await api.getArtifactPreviewBlob(projectId, jobId, artifactId);
            }
          } catch (err) {
            if (cancelled) return;
            const reason = err instanceof Error ? err.message : "overlay could not be loaded";
            setOverlayErrors((prev) => ({ ...prev, [layerType]: `${layer.display_name}: ${reason}` }));
            continue;
          }
          if (cancelled) return;
          const url = URL.createObjectURL(blob);
          objectUrlsRef.current[layerType] = url;
          overlay = L.imageOverlay(url, bounds, { opacity: opacity[layerType] });
          overlaysRef.current[layerType] = overlay;
        }
        overlay.setOpacity(opacity[layerType]);
        if (!map!.hasLayer(overlay)) overlay.addTo(map!);
      }
    }

    sync();
    return () => {
      cancelled = true;
    };
     
  }, [context, visibility, opacity, projectId, isGeoreferenced]);

  // P1-5: residual markers at the real sample locations (WGS84 lon/lat from
  // the stored GeoJSON). Rebuilt when the data, kind or interactivity changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (residualLayerRef.current) {
      map.removeLayer(residualLayerRef.current);
      residualLayerRef.current = null;
    }
    if (!residuals || !isGeoreferenced) return;

    const scaleMax = symmetricScaleMax(residuals.features, residuals.kind);
    const radius = residuals.referenceType === "gcp" ? 7 : 4;
    const group = L.layerGroup();
    for (const feature of residuals.features) {
      const [lon, lat] = feature.geometry.coordinates;
      const p = feature.properties;
      const marker = L.circleMarker([lat, lon], {
        pane: RESIDUAL_PANE,
        radius,
        fillColor: residualColor(residualValue(p, residuals.kind), scaleMax),
        fillOpacity: 0.95,
        color: p.inlier_in_production_fit ? "#475569" : "#000000",
        weight: p.inlier_in_production_fit ? 0.5 : 2,
        className: `residual-marker residual-sample-${p.sample_index}`,
        interactive: residuals.interactive,
        bubblingMouseEvents: false,
      });
      if (residuals.interactive) {
        marker.bindPopup(() => {
          const el = document.createElement("div");
          el.dataset.testid = "residual-tooltip";
          el.dataset.sampleIndex = String(p.sample_index);
          for (const line of residualTooltipLines(p, residuals.kind)) {
            const row = document.createElement("p");
            row.textContent = line;
            row.style.margin = "0";
            el.appendChild(row);
          }
          return el;
        });
      }
      marker.addTo(group);
    }
    group.addTo(map);
    residualLayerRef.current = group;
  }, [residuals, isGeoreferenced, context.dataset.id]);

  return (
    <div className="relative h-full w-full">
      {!isGeoreferenced && (
        <div className="absolute left-2 top-2 z-[1000] rounded-md border border-amber-200 bg-amber-100/95 px-2 py-1 text-xs font-medium text-amber-800 shadow animate-fade-in">
          Local coordinate view — source is not georeferenced.
        </div>
      )}
      {Object.values(overlayErrors).length > 0 && (
        <div
          data-testid="map-overlay-errors"
          className="absolute bottom-2 left-2 z-[1000] max-w-[70%] rounded-md border border-red-200 bg-red-50/95 px-2 py-1 text-xs text-red-700 shadow"
        >
          {Object.values(overlayErrors).map((message) => (
            <p key={message}>{message}</p>
          ))}
        </div>
      )}
      <div ref={containerRef} className="h-full w-full rounded-lg bg-slate-100" />
    </div>
  );
}
