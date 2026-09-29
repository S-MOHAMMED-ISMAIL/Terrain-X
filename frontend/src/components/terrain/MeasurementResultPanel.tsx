import { lazy, Suspense } from "react";
import type {
  CoordinateResult,
  DistanceResult,
  PointElevationResult,
  PointSlopeResult,
  ProfileResult,
} from "@/api/types";
import { Button, Card, Spinner } from "@/components/ui";
import { formatCoordinate, formatSlopeResult } from "./measurementFormat";

const ProfileChart = lazy(() =>
  import("./ProfileChart").then((module) => ({ default: module.ProfileChart })),
);

export type MeasurementResult =
  | { mode: "point"; data: PointElevationResult }
  | { mode: "distance"; data: DistanceResult }
  | { mode: "profile"; data: ProfileResult }
  | { mode: "coordinate"; data: CoordinateResult }
  | { mode: "slope"; data: PointSlopeResult };

interface Props {
  result: MeasurementResult | null;
  loading: boolean;
  error: string | null;
  onSave: () => void;
  saving: boolean;
}

/** Renders exactly one real, already-computed measurement result — every
 * number here came from the backend's real raster/CRS calculation
 * (app/services/measurement_service.py); nothing is derived or guessed
 * client-side. See docs/ARCHITECTURE.md §3.7 for the full scientific-honesty
 * rules this panel must never violate (never call relative depth
 * "elevation," never claim metres for an uncalibrated/unspecified-unit
 * value, never call a result survey-grade). */
export function MeasurementResultPanel({ result, loading, error, onSave, saving }: Props) {
  if (loading) {
    return (
      <Card padding="sm" className="flex items-center gap-2 text-xs text-slate-500 animate-fade-in">
        <Spinner /> Computing…
      </Card>
    );
  }
  if (error) {
    return (
      <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 animate-fade-in">
        {error}
      </div>
    );
  }
  if (!result) {
    return null;
  }

  return (
    <Card padding="sm" className="flex flex-col gap-2 text-xs animate-fade-in-up">
      <h3 className="text-sm font-semibold text-slate-900">Measurement result</h3>

      {result.mode === "point" && (
        <>
          <p className="text-slate-700">
            {result.data.value === null
              ? "No data at this point."
              : `${result.data.value_kind === "elevation" ? "Elevation" : "Relative depth"}: ${result.data.value.toFixed(3)}`}
          </p>
          <p className="text-slate-500">Units: {result.data.units}</p>
          <p className="text-slate-500">Calibration state: {result.data.calibration_state}</p>
          <p className="text-slate-500">{formatCoordinate(result.data.coordinate)}</p>
          <p className="text-metadata leading-snug text-slate-500">{result.data.disclaimer}</p>
        </>
      )}

      {result.mode === "distance" && (
        <>
          <p className="text-slate-700">
            {result.data.distance === null
              ? `${result.data.pixel_distance.toFixed(2)} pixels (not georeferenced)`
              : `${result.data.distance.toFixed(3)} ${result.data.units}`}
          </p>
          {result.data.reprojected && (
            <p className="text-slate-500">
              Reprojected to local CRS {result.data.local_crs} for this calculation (source CRS{" "}
              {result.data.crs} is geographic).
            </p>
          )}
          <p className="text-slate-500">Point 1: {formatCoordinate(result.data.point1)}</p>
          <p className="text-slate-500">Point 2: {formatCoordinate(result.data.point2)}</p>
          <p className="text-metadata leading-snug text-slate-500">{result.data.disclaimer}</p>
        </>
      )}

      {result.mode === "coordinate" && <p className="text-slate-700">{formatCoordinate(result.data)}</p>}

      {result.mode === "slope" && (
        <>
          <p className="text-slate-700">{formatSlopeResult(result.data)}</p>
          <p className="text-slate-500">Units: {result.data.units}</p>
          <p className="text-slate-500">Value kind: {result.data.value_kind} (not elevation)</p>
          {result.data.coordinate && (
            <p className="text-slate-500">{formatCoordinate(result.data.coordinate)}</p>
          )}
          <p className="text-slate-500">
            Slope raster pixel ({result.data.row}, {result.data.col}) · source{" "}
            {result.data.source_artifact_type ?? "unknown"}
            {result.data.reprojected_for_analysis ? " · reprojected to local UTM for analysis" : ""}
          </p>
          <p className="text-metadata leading-snug text-slate-500">{result.data.disclaimer}</p>
        </>
      )}

      {result.mode === "profile" && (
        <>
          <p className="text-slate-700">
            {result.data.sample_count} samples over {result.data.total_distance.toFixed(2)}{" "}
            {result.data.distance_units}
          </p>
          <p className="text-slate-500">
            Value: {result.data.value_kind === "elevation" ? "elevation" : "relative depth"} (
            {result.data.value_units})
          </p>
          <Suspense fallback={<div role="status" className="py-3 text-slate-500">Loading profile chart...</div>}>
            <ProfileChart
              samples={result.data.samples}
              distanceUnits={result.data.distance_units}
              valueUnits={result.data.value_units}
              valueKind={result.data.value_kind}
            />
          </Suspense>
          <p className="text-metadata leading-snug text-slate-500">{result.data.disclaimer}</p>
        </>
      )}

      <Button onClick={onSave} disabled={saving} className="mt-1 flex items-center gap-2 self-start">
        {saving && <Spinner />}
        {saving ? "Saving…" : "Save measurement"}
      </Button>
    </Card>
  );
}
