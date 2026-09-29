import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import type { Project } from "@/api/types";
import { AnimatedNumber, Button, Card, EmptyState } from "@/components/ui";
import { useBackgroundVariant } from "@/components/PageHeaderContext";
import { summarizeDashboardCounts, type DashboardCounts } from "./dashboardStats";

const RECENT_PROJECT_LIMIT = 5;

interface DashboardData {
  counts: DashboardCounts;
  projects: Project[];
}

let dashboardRequest: Promise<DashboardData> | null = null;

function loadDashboardData(): Promise<DashboardData> {
  if (!dashboardRequest) {
    dashboardRequest = api.listProjects().then(async (projects) => {
      const perProject = await Promise.all(
        projects.map(async (project) => ({
          datasets: await api.listDatasets(project.id),
          analysisJobs: await api.listAnalysisJobs(project.id),
        })),
      );
      return { counts: summarizeDashboardCounts(perProject), projects };
    });
    void dashboardRequest.then(
      () => { dashboardRequest = null; },
      () => { dashboardRequest = null; },
    );
  }
  return dashboardRequest;
}

function ProjectsIconLarge() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
      <path
        d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4l2 2h9A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5v-11Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DatasetsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
      <rect x="3" y="4" width="18" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
      <rect x="3" y="10" width="18" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
      <rect x="3" y="16" width="18" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

function AnalysisIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
      <path
        d="M4 19V9M12 19V5M20 19v-6"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function DashboardPage() {
  useBackgroundVariant("dashboard");
  const { user } = useAuth();
  const [counts, setCounts] = useState<DashboardCounts | null>(null);
  const [recentProjects, setRecentProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadCounts() {
      try {
        const { counts: nextCounts, projects } = await loadDashboardData();
        if (cancelled) return;
        setCounts(nextCounts);
        setRecentProjects(projects.slice(0, RECENT_PROJECT_LIMIT));
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Failed to load data");
        }
      }
    }

    loadCounts();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <h1 className="mb-1 text-2xl font-semibold tracking-tight text-white">
        Welcome{user ? `, ${user.email}` : ""}
      </h1>
      <p className="mb-8 text-sm text-slate-400">
        Single-view terrain reconstruction and disaster intelligence — monocular depth, optional
        DEM/GCP calibration, interactive 3D terrain, measurements, and screening.
      </p>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 animate-fade-in">
          {error}
        </div>
      )}

      <div className="stagger-children grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card interactive>
          <div className="mb-2 flex items-center gap-2 text-slate-500">
            <ProjectsIconLarge />
            <p className="text-sm text-slate-500">Projects</p>
          </div>
          <p className="text-3xl font-semibold tabular-nums text-slate-900">
            <AnimatedNumber value={counts?.projects ?? null} />
          </p>
        </Card>
        <Card interactive>
          <div className="mb-2 flex items-center gap-2 text-slate-500">
            <DatasetsIcon />
            <p className="text-sm text-slate-500">Datasets</p>
          </div>
          <p className="text-3xl font-semibold tabular-nums text-slate-900">
            <AnimatedNumber value={counts?.datasets ?? null} />
          </p>
          <p className="mt-1 text-xs text-slate-500">Across all your projects</p>
        </Card>
        <Card interactive>
          <div className="mb-2 flex items-center gap-2 text-slate-500">
            <AnalysisIcon />
            <p className="text-sm text-slate-500">Analysis jobs</p>
          </div>
          <p className="text-3xl font-semibold tabular-nums text-slate-900">
            <AnimatedNumber value={counts?.analysisJobs ?? null} />
          </p>
          <p className="mt-1 text-xs text-slate-500">Across all your projects</p>
        </Card>
      </div>

      <div className="mt-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            Recent projects
          </h2>
          <Link to="/projects" className="text-sm font-medium text-brand-400 hover:underline">
            View all →
          </Link>
        </div>

        {recentProjects === null ? (
          <div className="flex flex-col gap-2">
            <div className="h-14 w-full skeleton" />
            <div className="h-14 w-full skeleton" />
          </div>
        ) : recentProjects.length === 0 ? (
          <EmptyState
            title="No projects yet"
            description="Create a project to upload your first image and run a real terrain analysis."
            action={
              <Link to="/projects">
                <Button>Create a project</Button>
              </Link>
            }
          />
        ) : (
          <ul className="stagger-children divide-y divide-slate-200 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-panel">
            {recentProjects.map((project) => (
              <li key={project.id}>
                <Link
                  to={`/projects/${project.id}`}
                  className="flex items-center justify-between px-4 py-3 transition-colors hover:bg-slate-50"
                >
                  <div>
                    <p className="font-medium text-slate-900">{project.name}</p>
                    {project.description && (
                      <p className="text-sm text-slate-500">{project.description}</p>
                    )}
                  </div>
                  <span className="text-xs text-slate-500">
                    {new Date(project.created_at).toLocaleDateString()}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
