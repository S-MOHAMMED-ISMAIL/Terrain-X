import { useEffect, useState } from "react";
import { ApiError, api } from "@/api/client";
import type { Measurement } from "@/api/types";
import { Button, Card } from "@/components/ui";
import { formatMeasurementSummary, measurementTypeLabel } from "./measurementFormat";

interface Props {
  projectId: string;
  // Bump to force a re-fetch after a new measurement is saved elsewhere.
  refreshToken: number;
}

/** Real saved measurement rows (backend/app/models/measurement.py) — list
 * and delete only; a measurement's result is never recomputed or edited
 * client-side, since it's a real, immutable server-computed record. */
export function MeasurementHistory({ projectId, refreshToken }: Props) {
  const [measurements, setMeasurements] = useState<Measurement[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listMeasurements(projectId)
      .then((data) => {
        if (!cancelled) setMeasurements(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load measurements");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, refreshToken]);

  async function handleDelete(id: string) {
    try {
      await api.deleteMeasurement(projectId, id);
      setMeasurements((prev) => (prev ?? []).filter((m) => m.id !== id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete measurement");
    }
  }

  return (
    <Card padding="sm">
      <h3 className="mb-2 text-sm font-semibold text-slate-900">Saved measurements</h3>
      {error && <p className="mb-2 text-xs text-red-600">{error}</p>}
      {measurements === null ? (
        <div className="h-10 w-full skeleton" />
      ) : measurements.length === 0 ? (
        <p className="text-xs text-slate-500">No saved measurements yet for this project.</p>
      ) : (
        <ul className="stagger-children flex max-h-56 flex-col gap-1 overflow-y-auto text-xs">
          {measurements.map((m) => (
            <li
              key={m.id}
              className="flex items-center justify-between gap-2 border-b border-slate-100 pb-1"
            >
              <div>
                <span className="font-medium text-slate-700">
                  {measurementTypeLabel(m.measurement_type)}
                </span>
                <span className="ml-2 text-slate-500">{formatMeasurementSummary(m)}</span>
              </div>
              <Button variant="danger" onClick={() => handleDelete(m.id)} className="shrink-0">
                Delete
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
