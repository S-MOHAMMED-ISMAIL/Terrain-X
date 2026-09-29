import { useRef, useState } from "react";
import { ApiError, api } from "@/api/client";
import type { Dataset, DatasetRole } from "@/api/types";
import { Badge, ProgressBar } from "@/components/ui";

interface Props {
  projectId: string;
  onUploaded: (dataset: Dataset) => void;
}

const ROLE_OPTIONS: { value: DatasetRole; label: string; accept: string; hint: string }[] = [
  {
    value: "source_image",
    label: "Source image",
    accept: ".jpg,.jpeg,.png,.tif,.tiff,image/jpeg,image/png,image/tiff",
    hint: "Supported formats: JPEG, PNG, TIFF/GeoTIFF. GeoTIFFs with embedded CRS are detected automatically.",
  },
  {
    value: "dem_reference",
    label: "DEM reference",
    accept: ".tif,.tiff,image/tiff",
    hint: "A real georeferenced elevation raster (GeoTIFF). Must have a valid CRS — a plain, non-georeferenced raster is rejected, since it can't be aligned to a source image.",
  },
  {
    value: "gcp_reference",
    label: "GCP reference (CSV)",
    accept: ".csv,text/csv",
    hint: "A CSV with an x,y,z header row (case-insensitive). Declare the CRS of the x,y columns below — a CSV carries no CRS of its own.",
  },
];

export function DatasetUpload({ projectId, onUploaded }: Props) {
  const [role, setRole] = useState<DatasetRole>("source_image");
  const [gcpCrs, setGcpCrs] = useState("EPSG:4326");
  const [error, setError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [justUploaded, setJustUploaded] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const roleOption = ROLE_OPTIONS.find((o) => o.value === role)!;
  const gcpReady = role !== "gcp_reference" || gcpCrs.trim() !== "";

  async function handleFile(file: File) {
    setError(null);
    setJustUploaded(null);
    setIsUploading(true);
    setProgress(0);
    try {
      const dataset = await api.uploadDataset(
        projectId,
        file,
        (loaded, total) => {
          setProgress(Math.round((loaded / total) * 100));
        },
        { role, gcpCrs: role === "gcp_reference" ? gcpCrs : undefined },
      );
      onUploaded(dataset);
      setJustUploaded(dataset.original_filename);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setIsUploading(false);
      setProgress(0);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function handleInputChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) handleFile(file);
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragOver(false);
    if (isUploading || !gcpReady) return;
    const file = event.dataTransfer.files?.[0];
    if (file) handleFile(file);
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-panel">
      <label className="mb-1 block text-sm font-medium text-slate-700">Upload dataset</label>

      <div className="mb-3 flex gap-2">
        {ROLE_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => setRole(option.value)}
            disabled={isUploading}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors duration-150 ${
              role === option.value
                ? "bg-slate-900 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            } disabled:opacity-50`}
          >
            {option.label}
          </button>
        ))}
      </div>

      <p className="mb-3 text-xs text-slate-500">{roleOption.hint}</p>

      {role === "gcp_reference" && (
        <div className="mb-3">
          <label className="mb-1 block text-xs font-medium text-slate-700">
            CRS of the CSV&apos;s x,y coordinates
          </label>
          <input
            type="text"
            value={gcpCrs}
            onChange={(e) => setGcpCrs(e.target.value)}
            placeholder="e.g. EPSG:4326 or EPSG:32643"
            disabled={isUploading}
            className="w-64 rounded-md border border-slate-300 px-3 py-1.5 text-sm transition-colors focus:border-brand-500 disabled:opacity-50"
          />
        </div>
      )}

      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!isUploading && gcpReady) setIsDragOver(true);
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
        onClick={() => !isUploading && gcpReady && inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors duration-150 ${
          isDragOver
            ? "border-brand-500 bg-brand-50"
            : "border-slate-300 bg-slate-50/50 hover:border-slate-400"
        } ${isUploading || !gcpReady ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
      >
        <svg viewBox="0 0 24 24" fill="none" className="h-7 w-7 text-slate-400" aria-hidden="true">
          <path
            d="M12 16V4m0 0 4 4m-4-4-4 4"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M4 16v2.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V16"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
        <p className="text-sm text-slate-600">
          <span className="font-medium text-slate-900">Click to browse</span> or drag a file here
        </p>
        <input
          ref={inputRef}
          type="file"
          accept={roleOption.accept}
          disabled={isUploading || !gcpReady}
          onChange={handleInputChange}
          className="hidden"
        />
      </div>

      {isUploading && (
        <div className="mt-3 animate-fade-in">
          <ProgressBar value={progress} />
          <p className="mt-1 text-xs text-slate-500">Uploading… {progress}%</p>
        </div>
      )}

      {justUploaded && !isUploading && (
        <div className="mt-3 flex items-center gap-2 animate-fade-in">
          <Badge tone="success" dot>
            Uploaded
          </Badge>
          <span className="text-xs text-slate-500">{justUploaded}</span>
        </div>
      )}

      {error && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 animate-fade-in">
          {error}
        </div>
      )}
    </div>
  );
}
