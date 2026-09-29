import { Button, Card, LiveStatus, Notice } from "@/components/ui";
import {
  MEASUREMENT_MODE_LABELS,
  pointsRemaining,
  type MeasurementMode,
  type MeasurementPoint,
} from "./measurementMode";
import { WorkspaceToolSection } from "./WorkspaceToolSection";

interface Props {
  mode: "2d" | "3d";
  measurementMode: MeasurementMode;
  points: MeasurementPoint[];
  loading: boolean;
  unavailableReason?: string | null;
  onModeChange: (mode: MeasurementMode) => void;
  onClear: () => void;
}

const TOOL_LABELS: Record<MeasurementMode, string> = {
  off: "Inspect (off)",
  point: "Point elevation",
  distance: "Distance",
  profile: "Profile",
  coordinate: "Coordinate",
  slope: "Slope at point",
};

function instruction(mode: MeasurementMode, points: MeasurementPoint[], surface: string): string {
  if (mode === "off") return `Click the ${surface} to inspect the active visible layer.`;
  const remaining = pointsRemaining(mode, points);
  if (remaining > 0) return `Click ${remaining} more point${remaining === 1 ? "" : "s"} on the ${surface}.`;
  return "Click registered. Reading the terrain result.";
}

export function MeasurementTools({
  mode,
  measurementMode,
  points,
  loading,
  unavailableReason,
  onModeChange,
  onClear,
}: Props) {
  const activeLabel = TOOL_LABELS[measurementMode];
  const hasPendingMeasurement = measurementMode !== "off" || points.length > 0;

  return (
    <Card padding="sm">
      <WorkspaceToolSection
        title="Measurement"
        status={unavailableReason ? "Unavailable" : `${activeLabel} active`}
        tone={unavailableReason ? "warning" : measurementMode === "off" ? "info" : "brand"}
        description="Choose one tool, then work directly in the terrain viewport."
        testId="measurement-tools"
      >
        {unavailableReason ? (
          <Notice tone="warning" title="Measurement unavailable">
            {unavailableReason}
          </Notice>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-1.5" role="group" aria-label="Measurement tool">
              {(Object.keys(MEASUREMENT_MODE_LABELS) as MeasurementMode[]).map((tool) => (
                <button
                  key={tool}
                  type="button"
                  aria-pressed={measurementMode === tool}
                  onClick={() => onModeChange(tool)}
                  className={`min-h-control rounded-control border px-2 text-metadata font-medium transition-colors duration-fast focus-visible:ring-offset-white ${
                    measurementMode === tool
                      ? "border-amber-500 bg-amber-100 text-amber-950"
                      : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50"
                  }`}
                >
                  {TOOL_LABELS[tool]}
                </button>
              ))}
            </div>
            <LiveStatus
              data-testid="measurement-instruction"
              className="mt-2 rounded-control border border-sky-200 bg-sky-50 px-2 py-1.5 text-metadata text-sky-900"
            >
              <span className="font-semibold">Measurement mode: {activeLabel}.</span>{" "}
              {loading ? "Reading terrain value..." : instruction(measurementMode, points, mode === "2d" ? "map" : "terrain")}
            </LiveStatus>
            <div className="mt-2 flex justify-end">
              <Button size="sm" variant="ghost" onClick={onClear} disabled={!hasPendingMeasurement}>
                Clear measurement
              </Button>
            </div>
          </>
        )}
      </WorkspaceToolSection>
    </Card>
  );
}
