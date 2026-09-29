import type { BoundingBox, LayerContext } from "@/api/types";

// P1-6: pure 2D-map click / overlay-placement logic, kept free of Leaflet
// and React imports so it is unit-testable (same pattern as inspection.ts).
//
// A georeferenced map click is only ever a real map coordinate (lng/lat):
// the frontend never turns it into a raster row/col itself. The source
// pixel is resolved by the backend (`/measurements/pixel`, i.e.
// geospatial.measurements.coordinate_to_pixel) against the authoritative
// raster of the exact layer being sampled — each layer's own CRS/transform.
// A 3D click or a non-georeferenced (CRS.Simple) click already IS a source
// pixel and is used as-is.

export interface MapClick {
  row?: number;
  col?: number;
  lat?: number;
  lng?: number;
}

export interface SourcePixel {
  row: number;
  col: number;
}

export interface PixelResolution {
  row: number;
  col: number;
  in_bounds: boolean;
}

/** Resolves (lng, lat) in EPSG:4326 against one layer's own raster. */
export type PixelResolver = (lng: number, lat: number) => Promise<PixelResolution>;

/** What a raw Leaflet click becomes. Georeferenced: the coordinate only.
 * CRS.Simple (non-georeferenced): the existing local pixel mapping. */
export function mapClickFromLatLng(lat: number, lng: number, isGeoreferenced: boolean): MapClick {
  if (isGeoreferenced) return { lat, lng };
  return { row: Math.round(lat), col: Math.round(lng) };
}

/** The clicked point as a source pixel of the layer `resolve` belongs to,
 * or null when the point lies outside that raster. */
export async function resolveToSourcePixel(
  click: MapClick,
  resolve: PixelResolver,
): Promise<SourcePixel | null> {
  if (click.row !== undefined && click.col !== undefined) {
    return { row: click.row, col: click.col };
  }
  if (click.lat === undefined || click.lng === undefined) {
    throw new Error("A map click needs either a source pixel or a map coordinate.");
  }
  const pixel = await resolve(click.lng, click.lat);
  return pixel.in_bounds ? { row: pixel.row, col: pixel.col } : null;
}

export type LatLngCorners = [[number, number], [number, number]];

function corners(b: BoundingBox): LatLngCorners {
  return [
    [b.min_y, b.min_x],
    [b.max_y, b.max_x],
  ];
}

/** Where a layer's overlay image goes. Georeferenced: that layer's OWN
 * EPSG:3857 overlay-grid corners, or null when the backend reported none
 * (the layer is then not drawn — never stretched over some other box).
 * Non-georeferenced: local pixel space, unchanged. */
export function overlayCorners(
  layer: Pick<LayerContext, "map_overlay_bounds">,
  isGeoreferenced: boolean,
  datasetSize: { width: number | null; height: number | null },
): LatLngCorners | null {
  if (isGeoreferenced) {
    return layer.map_overlay_bounds ? corners(layer.map_overlay_bounds) : null;
  }
  return [
    [0, 0],
    [datasetSize.height ?? 1, datasetSize.width ?? 1],
  ];
}
