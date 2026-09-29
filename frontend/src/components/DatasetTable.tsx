import { useState } from "react";
import { ApiError, api } from "@/api/client";
import type { Dataset, DatasetStatus } from "@/api/types";
import { Badge, type BadgeTone, Button, EmptyState } from "@/components/ui";
import { formatBytes } from "@/utils/format";

const STATUS_TONE: Record<DatasetStatus, BadgeTone> = {
  uploaded: "neutral",
  validating: "warning",
  valid: "success",
  invalid: "danger",
  failed: "danger",
};

function StatusBadge({ dataset }: { dataset: Dataset }) {
  return (
    <Badge tone={STATUS_TONE[dataset.status]} title={dataset.validation_error ?? undefined}>
      {dataset.status}
    </Badge>
  );
}

function GeoreferenceInfo({ dataset }: { dataset: Dataset }) {
  if (dataset.status !== "valid") {
    return <span className="text-slate-400">—</span>;
  }
  if (dataset.role === "gcp_reference") {
    return (
      <span className="text-sm text-slate-700">
        {dataset.gcp_point_count ?? 0} point(s){dataset.gcp_crs ? ` (${dataset.gcp_crs})` : ""}
      </span>
    );
  }
  if (dataset.is_georeferenced) {
    return (
      <span className="text-sm text-slate-700">
        Georeferenced{dataset.crs ? ` (${dataset.crs})` : ""}
      </span>
    );
  }
  return <span className="text-sm text-slate-500">Non-georeferenced imagery</span>;
}

const ROLE_LABELS: Record<Dataset["role"], string> = {
  source_image: "Source image",
  dem_reference: "DEM reference",
  gcp_reference: "GCP reference",
};

const ROLE_STYLES: Record<Dataset["role"], string> = {
  source_image: "bg-slate-100 text-slate-600",
  dem_reference: "bg-indigo-100 text-indigo-700",
  gcp_reference: "bg-purple-100 text-purple-700",
};

function RoleBadge({ dataset }: { dataset: Dataset }) {
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${ROLE_STYLES[dataset.role]}`}
    >
      {ROLE_LABELS[dataset.role]}
    </span>
  );
}

interface Props {
  projectId: string;
  datasets: Dataset[];
  onDeleted: (datasetId: string) => void;
}

export function DatasetTable({ projectId, datasets, onDeleted }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  async function handleDelete(datasetId: string) {
    setError(null);
    setPendingId(datasetId);
    try {
      await api.deleteDataset(projectId, datasetId);
      onDeleted(datasetId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete dataset");
    } finally {
      setPendingId(null);
    }
  }

  async function handleDownload(dataset: Dataset) {
    setError(null);
    try {
      await api.downloadDataset(projectId, dataset.id, dataset.original_filename);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to download dataset");
    }
  }

  if (datasets.length === 0) {
    return <EmptyState title="No datasets yet" description="Upload one above to get started." />;
  }

  return (
    <div>
      {error && (
        <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 animate-fade-in">
          {error}
        </div>
      )}
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-panel animate-fade-in">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2 font-medium">Filename</th>
              <th className="px-4 py-2 font-medium">Role</th>
              <th className="px-4 py-2 font-medium">Type</th>
              <th className="px-4 py-2 font-medium">Size</th>
              <th className="px-4 py-2 font-medium">Dimensions</th>
              <th className="px-4 py-2 font-medium">Georeferencing</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 stagger-children">
            {datasets.map((dataset) => (
              <tr key={dataset.id} className="transition-colors hover:bg-slate-50/70">
                <td className="px-4 py-2 text-slate-900">{dataset.original_filename}</td>
                <td className="px-4 py-2">
                  <RoleBadge dataset={dataset} />
                </td>
                <td className="px-4 py-2 uppercase text-slate-600">{dataset.file_type}</td>
                <td className="px-4 py-2 text-slate-600">
                  {formatBytes(dataset.file_size_bytes)}
                </td>
                <td className="px-4 py-2 text-slate-600">
                  {dataset.width && dataset.height ? `${dataset.width} × ${dataset.height}` : "—"}
                </td>
                <td className="px-4 py-2">
                  <GeoreferenceInfo dataset={dataset} />
                </td>
                <td className="px-4 py-2">
                  <StatusBadge dataset={dataset} />
                </td>
                <td className="px-4 py-2">
                  <div className="flex gap-3">
                    <Button variant="ghost" onClick={() => handleDownload(dataset)}>
                      Download
                    </Button>
                    <Button
                      variant="danger"
                      onClick={() => handleDelete(dataset.id)}
                      disabled={pendingId === dataset.id}
                    >
                      Delete
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
