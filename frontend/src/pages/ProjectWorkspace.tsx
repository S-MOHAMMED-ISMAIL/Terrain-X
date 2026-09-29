import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "@/api/client";
import type { Dataset, Project } from "@/api/types";
import { AnalysisPanel } from "@/components/AnalysisPanel";
import { DatasetTable } from "@/components/DatasetTable";
import { DatasetUpload } from "@/components/DatasetUpload";
import {
  type BackgroundVariant,
  useBackgroundVariant,
  usePageBreadcrumb,
} from "@/components/PageHeaderContext";
import { ReportsPanel } from "@/components/ReportsPanel";
import { TerrainWorkspace } from "@/components/terrain/TerrainWorkspace";
import type { WorkspaceMode } from "@/components/terrain/TerrainWorkspaceShell";
import { TABS, tabRoutesToTerrainWorkspace, type Tab } from "./workspaceTabs";

// Purely decorative — which ambient ScientificBackground mood the currently
// active tab suggests. Never affects real data/behavior.
const TAB_BACKGROUND_VARIANT: Record<Tab, BackgroundVariant> = {
  datasets: "default",
  analysis: "analysis",
  terrain: "terrain",
  measurements: "terrain",
  disaster: "disaster",
  reports: "reports",
};

export function ProjectWorkspacePage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [datasets, setDatasets] = useState<Dataset[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("datasets");
  const [workspaceMounted, setWorkspaceMounted] = useState(false);
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>("explore");
  const [workspaceDatasetId, setWorkspaceDatasetId] = useState<string>();

  useBackgroundVariant(TAB_BACKGROUND_VARIANT[activeTab]);
  usePageBreadcrumb(
    project
      ? [{ label: "Projects", to: "/projects" }, { label: project.name }]
      : [{ label: "Projects", to: "/projects" }],
  );

  useEffect(() => {
    if (!projectId) return;
    api
      .getProject(projectId)
      .then(setProject)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to load project"));
    api
      .listDatasets(projectId)
      .then(setDatasets)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Failed to load datasets"));
  }, [projectId]);

  if (!projectId) return null;

  const showsSharedWorkspaceNote =
    activeTab === "terrain" || activeTab === "measurements" || activeTab === "disaster";
  function selectTab(tab: Tab, datasetId?: string) {
    if (tabRoutesToTerrainWorkspace(tab)) {
      setWorkspaceMounted(true);
      setWorkspaceMode(tab === "measurements" ? "measure" : tab === "disaster" ? "screen" : "explore");
      if (datasetId) setWorkspaceDatasetId(datasetId);
    }
    setActiveTab(tab);
  }

  return (
    <div>
      {!showsSharedWorkspaceNote && <Link
        to="/projects"
        className="mb-4 inline-flex items-center gap-1 text-sm text-slate-400 transition-colors hover:text-slate-200"
      >
        <span aria-hidden="true">←</span> All projects
      </Link>}

      {!showsSharedWorkspaceNote && (project ? (
        <div className="mb-6 animate-fade-in">
          <h1 className="text-2xl font-semibold tracking-tight text-white">{project.name}</h1>
          {project.description && <p className="text-sm text-slate-400">{project.description}</p>}
        </div>
      ) : (
        <div className="mb-6 h-9 w-64 skeleton" />
      ))}

      <div className="mb-2 flex flex-wrap gap-1 border-b border-terrain-800">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            disabled={!tab.enabled}
            onClick={() => tab.enabled && selectTab(tab.id)}
            title={tab.enabled ? undefined : "Coming in a later phase"}
            aria-current={activeTab === tab.id ? "page" : undefined}
            className={`relative border-b-2 px-4 py-2 text-sm font-medium transition-colors duration-150 ${
              activeTab === tab.id
                ? "border-brand-500 text-white"
                : "border-transparent text-slate-400"
            } ${tab.enabled ? "hover:text-slate-200" : "cursor-not-allowed"}`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {showsSharedWorkspaceNote && (
        <p className="mb-2 px-1 text-xs text-slate-500">
          Terrain, Measurements, and Disaster Screening are one integrated 2D/3D workspace over the
          same dataset — switching between these tabs changes which tools are in focus, not the view
          itself.
        </p>
      )}

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 animate-fade-in">
          {error}
        </div>
      )}

      {/* No `key={activeTab}` here: "terrain"/"measurements"/"disaster" all
          render the same TerrainWorkspace instance so it can share its
          selected-dataset/visualization-context state across those three
          tabs (see tabRoutesToTerrainWorkspace) — keying this wrapper by
          activeTab would force React to unmount/remount that shared
          instance on every switch between them, silently resetting it. */}
      <div className="animate-fade-in">
        {activeTab === "datasets" && (
          <div className="flex flex-col gap-6">
            <DatasetUpload
              projectId={projectId}
              // Deduplicated by ID: the initial list fetch may already
              // include a dataset whose upload finished while it was in flight.
              onUploaded={(dataset) =>
                setDatasets((prev) => [dataset, ...(prev ?? []).filter((d) => d.id !== dataset.id)])
              }
            />
            {datasets === null ? (
              <div className="flex flex-col gap-2">
                <div className="h-10 w-full skeleton" />
                <div className="h-10 w-full skeleton" />
              </div>
            ) : (
              <DatasetTable
                projectId={projectId}
                datasets={datasets}
                onDeleted={(id) => setDatasets((prev) => (prev ?? []).filter((d) => d.id !== id))}
              />
            )}
          </div>
        )}

        {activeTab === "analysis" && (
          <AnalysisPanel
            projectId={projectId}
            datasets={datasets ?? []}
            onOpenWorkspace={(datasetId) => selectTab("terrain", datasetId)}
            onOpenReports={() => selectTab("reports")}
          />
        )}

        {activeTab === "reports" && (
          <ReportsPanel
            projectId={projectId}
            datasets={datasets ?? []}
            onOpenAnalysis={() => selectTab("analysis")}
          />
        )}

        {workspaceMounted && (
          <div hidden={!showsSharedWorkspaceNote} aria-hidden={!showsSharedWorkspaceNote || undefined}>
            <TerrainWorkspace
              projectId={projectId}
              projectName={project?.name ?? "Terrain workspace"}
              datasets={datasets ?? []}
              requestedMode={workspaceMode}
              requestedDatasetId={workspaceDatasetId}
            />
          </div>
        )}
      </div>
    </div>
  );
}
