import type { AnalysisJob, Dataset } from "@/api/types";

export interface DashboardCounts {
  projects: number;
  datasets: number;
  analysisJobs: number;
}

/** Real per-project data (whatever the existing, already-ownership-scoped
 * `listDatasets`/`listAnalysisJobs` API calls returned for one of the
 * current user's own projects) — never a hardcoded/mock count. */
export interface ProjectCounts {
  datasets: Dataset[];
  analysisJobs: AnalysisJob[];
}

/** Sums real per-project dataset/job counts into dashboard-level totals.
 * Pure arithmetic over whatever was actually fetched — a project with zero
 * datasets/jobs, or an empty project list altogether, correctly yields 0,
 * never a placeholder or an invented number. */
export function summarizeDashboardCounts(perProject: ProjectCounts[]): DashboardCounts {
  return {
    projects: perProject.length,
    datasets: perProject.reduce((sum, p) => sum + p.datasets.length, 0),
    analysisJobs: perProject.reduce((sum, p) => sum + p.analysisJobs.length, 0),
  };
}
