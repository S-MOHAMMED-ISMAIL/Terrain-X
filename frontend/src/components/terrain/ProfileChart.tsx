import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ProfileSample } from "@/api/types";

interface Props {
  samples: ProfileSample[];
  distanceUnits: string;
  valueUnits: string;
  valueKind: "elevation" | "relative_depth";
}

/** A real chart of backend-returned raster samples (geospatial/measurements.py
 * ::sample_profile) — every point plotted is an actual value read from the
 * stored raster, or a real gap where the value was NoData (`connectNulls`
 * is deliberately false, so a NoData run of samples shows as a real break
 * in the line, never bridged over as if the terrain were smooth there). */
export function ProfileChart({ samples, distanceUnits, valueUnits, valueKind }: Props) {
  const data = samples.map((s) => ({ distance: s.distance_along, value: s.value }));
  const valueLabel = valueKind === "elevation" ? "Elevation" : "Relative depth";

  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 20, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis
            dataKey="distance"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v: number) => v.toFixed(1)}
            tick={{ fontSize: 10 }}
            label={{
              value: `Distance (${distanceUnits})`,
              position: "insideBottom",
              offset: -12,
              fontSize: 11,
            }}
          />
          <YAxis
            tickFormatter={(v: number) => v.toFixed(2)}
            tick={{ fontSize: 10 }}
            label={{ value: valueLabel, angle: -90, position: "insideLeft", fontSize: 11 }}
            domain={["auto", "auto"]}
          />
          <Tooltip
            formatter={(value) => {
              const v = typeof value === "number" ? value : null;
              return [v === null ? "No data" : `${v.toFixed(3)} ${valueUnits}`, valueLabel];
            }}
            labelFormatter={(label) =>
              typeof label === "number" ? `Distance: ${label.toFixed(2)} ${distanceUnits}` : String(label)
            }
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke="#06998a"
            strokeWidth={1.75}
            dot={{ r: 2 }}
            connectNulls={false}
            // Deliberately not animated: this line is real, already-computed
            // scientific data (a raster profile sample), never a value that
            // should appear to "grow" or "resolve" over time — that would
            // visually imply an uncertainty/convergence process that isn't
            // real here.
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
