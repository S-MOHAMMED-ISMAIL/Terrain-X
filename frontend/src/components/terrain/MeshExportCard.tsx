import { useState } from "react";
import { ApiError, api } from "@/api/client";
import { Button, Card, LiveStatus, Notice, Select } from "@/components/ui";
import { downloadBlob } from "./flythroughRecorder";
import {
  type GlbSummary,
  MESH_EXPORT_RESOLUTIONS,
  type MeshExportResolution,
  meshExportFileName,
  readGlbSummary,
} from "./meshExport";
import { WorkspaceToolSection } from "./WorkspaceToolSection";

interface Props {
  projectId: string;
  jobId: string;
  artifactId: string;
}

/** P1-9: download the terrain grid as a physical GLB mesh. */
export function MeshExportCard({
  projectId,
  jobId,
  artifactId,
}: Props) {
  const [resolution, setResolution] = useState<MeshExportResolution>(256);
  const [texture, setTexture] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<GlbSummary | null>(null);

  async function exportMesh() {
    setBusy(true);
    setError(null);
    setSummary(null);
    try {
      const buffer = await api.getTerrainMeshGlb(projectId, jobId, artifactId, resolution, texture);
      const parsed = readGlbSummary(buffer);
      downloadBlob(new Blob([buffer], { type: "model/gltf-binary" }), meshExportFileName(artifactId, resolution));
      setSummary(parsed);
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : "Mesh export failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="mesh-export-card">
      <Card padding="sm" className="text-xs text-slate-600">
        <WorkspaceToolSection
          title="Export terrain"
          status={busy ? "Generating" : error ? "Failed" : summary ? "Completed" : "Ready"}
          tone={busy ? "warning" : error ? "danger" : summary ? "success" : "neutral"}
          description="Generate a physical GLB terrain-grid mesh from the current authoritative artifact."
        >
        <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label className="flex flex-col gap-1 font-medium text-slate-700">
            Resolution
            <Select
              aria-label="Mesh resolution"
              value={resolution}
              onChange={(e) => setResolution(Number(e.target.value) as MeshExportResolution)}
            >
              {MESH_EXPORT_RESOLUTIONS.map((r) => (
                <option key={r} value={r}>
                  {r} px
                </option>
              ))}
            </Select>
          </label>
          <label className="flex min-h-control items-center gap-2 self-end font-medium text-slate-700">
            <input
              type="checkbox"
              aria-label="Embed source texture"
              checked={texture}
              disabled={busy}
              onChange={(e) => setTexture(e.target.checked)}
            />
            Request compatible texture
          </label>
        </div>
        <p className="mb-2 text-metadata text-slate-500">
          Texture is embedded only when the GLB exporter confirms compatibility; the completed summary reports the actual result.
        </p>
        <p className="text-metadata text-slate-500">
          Physical terrain-grid mesh: cell-centre vertices with the raw grid values — no display
          exaggeration. NoData/sky stay holes. Different from the on-screen mesh.
        </p>
        {error && (
          <Notice data-testid="mesh-export-error" tone="error" title="GLB export failed" announce className="mt-2">
            {error} Check the current terrain artifact and try again.
          </Notice>
        )}
        {summary && (
          <LiveStatus data-testid="mesh-export-summary" className="mt-2 rounded-control border border-green-200 bg-green-50 px-2 py-1.5 text-metadata text-green-800">
            Exported {summary.triangleCount} triangles ({summary.vertexCount} vertices),{" "}
            {summary.heightKind === "elevation" ? "DSM" : "relative depth"} in {summary.verticalUnits}
            ; texture {summary.texture}
            {summary.textureOmittedReason ? ` (${summary.textureOmittedReason})` : ""}.
          </LiveStatus>
        )}
        <Button onClick={exportMesh} loading={busy} loadingLabel="Generating GLB..." className="mt-3 w-full">
          {error ? "Retry GLB export" : "Export 3D mesh (GLB)"}
        </Button>
        </WorkspaceToolSection>
      </Card>
    </div>
  );
}
