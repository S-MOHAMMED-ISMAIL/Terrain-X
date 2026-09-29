/**
 * Pure formatting helpers for real measurement results/history rows — kept
 * free of React/Leaflet/Three imports (same reason as `measurementMode.ts`)
 * so they're directly unit-testable. Every formatted string here is derived
 * from real API-returned data; nothing is fabricated or estimated.
 */

import type {
  CoordinateResult,
  DistanceResult,
  Measurement,
  PointElevationResult,
  PointSlopeResult,
  ProfileResult,
} from "@/api/types";

export function formatCoordinate(c: CoordinateResult): string {
  if (!c.is_georeferenced) {
    return `Pixel (${c.row.toFixed(0)}, ${c.col.toFixed(0)}) — not georeferenced`;
  }
  const native = `${c.native_x?.toFixed(2)}, ${c.native_y?.toFixed(2)} (${c.crs})`;
  const wgs84 =
    c.wgs84_lat !== null && c.wgs84_lon !== null
      ? ` · WGS84: ${c.wgs84_lat.toFixed(6)}, ${c.wgs84_lon.toFixed(6)}`
      : "";
  return native + wgs84;
}

const TYPE_LABELS: Record<Measurement["measurement_type"], string> = {
  point_elevation: "Point elevation",
  distance: "Distance",
  profile: "Profile",
  coordinate: "Coordinate",
  point_slope: "Slope at point",
};

/** P1-4: the primary line for a slope-at-point result — the stored slope
 * value exactly as the backend returned it (degrees, from the slope
 * artifact's own metadata), or the real reason there is none. Never
 * "Elevation", never a recomputed or interpolated number. */
export function formatSlopeResult(r: PointSlopeResult): string {
  if (r.value === null) {
    return r.in_bounds ? "No data at this point." : "Outside the slope raster.";
  }
  const suffix = r.units === "degrees" ? "°" : ` ${r.units}`;
  return `Slope: ${r.value.toFixed(2)}${suffix}`;
}

export function measurementTypeLabel(type: Measurement["measurement_type"]): string {
  return TYPE_LABELS[type];
}

/** A one-line real-value summary for a saved measurement's history row —
 * every number here is read directly from `result_data` exactly as the
 * backend computed and persisted it. */
export function formatMeasurementSummary(m: Measurement): string {
  switch (m.measurement_type) {
    case "point_elevation": {
      const r = m.result_data as unknown as PointElevationResult;
      return r.value === null
        ? "No data"
        : `${r.value.toFixed(3)} (${r.value_kind === "elevation" ? "elevation" : "relative depth"})`;
    }
    case "distance": {
      const r = m.result_data as unknown as DistanceResult;
      return r.distance === null
        ? `${r.pixel_distance.toFixed(1)} px`
        : `${r.distance.toFixed(2)} ${r.units}`;
    }
    case "profile": {
      const r = m.result_data as unknown as ProfileResult;
      return `${r.sample_count} samples, ${r.total_distance.toFixed(2)} ${r.distance_units}`;
    }
    case "point_slope": {
      const r = m.result_data as unknown as PointSlopeResult;
      if (r.value === null) return r.in_bounds ? "No data" : "Outside slope raster";
      return `${r.value.toFixed(2)}${r.units === "degrees" ? "°" : ` ${r.units}`} slope`;
    }
    case "coordinate": {
      const r = m.result_data as unknown as CoordinateResult;
      return r.wgs84_lat !== null && r.wgs84_lon !== null
        ? `${r.wgs84_lat.toFixed(5)}, ${r.wgs84_lon.toFixed(5)}`
        : `pixel (${r.row.toFixed(0)}, ${r.col.toFixed(0)})`;
    }
    default:
      return "";
  }
}
