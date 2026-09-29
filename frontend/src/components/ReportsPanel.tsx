import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/api/client";
import type { Dataset, Report } from "@/api/types";
import { Badge, type BadgeTone, Button, Card, EmptyState, LoadingState, Notice, Spinner } from "@/components/ui";
import { isReportActive, reportStatusLabel } from "./reportFormat";

const POLL_INTERVAL_MS = Number(import.meta.env.VITE_ANALYSIS_POLL_INTERVAL_MS) || 3000;
const STATUS_TONE: Record<Report["status"], BadgeTone> = { pending: "neutral", generating: "warning", completed: "success", failed: "danger" };
const hasActiveReport = (reports: Report[] | null) => (reports ?? []).some((report) => isReportActive(report.status));

interface Props { projectId: string; datasets: Dataset[]; onOpenAnalysis: () => void }

/** Metadata-only R1 lifecycle UI. Full report contents are never fetched for this list. */
export function ReportsPanel({ projectId, datasets, onOpenAnalysis }: Props) {
  const validDatasets = datasets.filter((dataset) => dataset.status === "valid" && dataset.role === "source_image");
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [reports, setReports] = useState<Report[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    if (!selectedDatasetId) { setReports(null); return null; }
    try { const data = await api.listReports(projectId, selectedDatasetId); setReports(data); return data; }
    catch (err) { setError(err instanceof ApiError ? err.message : "Failed to load reports"); return null; }
  }, [projectId, selectedDatasetId]);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    const stop = () => { if (intervalRef.current) clearInterval(intervalRef.current); intervalRef.current = null; };
    if (!hasActiveReport(reports)) { stop(); return stop; }
    if (!intervalRef.current) intervalRef.current = setInterval(async () => { if (!hasActiveReport(await refresh())) stop(); }, POLL_INTERVAL_MS);
    return stop;
  }, [reports, refresh]);

  async function handleGenerate() {
    if (!selectedDatasetId) return;
    setError(null); setCreating(true);
    try { await api.createReport(projectId, selectedDatasetId); await refresh(); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Failed to start report generation"); }
    finally { setCreating(false); }
  }
  async function handleDownload(reportId: string, kind: "pdf" | "json" | "csv" | "bundle") {
    setError(null); setDownloadingId(`${reportId}:${kind}`);
    try {
      if (kind === "pdf") await api.downloadReportPdf(projectId, reportId);
      else if (kind === "json") await api.downloadReportJson(projectId, reportId);
      else if (kind === "csv") await api.downloadReportCsv(projectId, reportId);
      else await api.downloadReportBundle(projectId, reportId);
    } catch (err) { setError(err instanceof ApiError ? err.message : `Failed to download ${kind}`); }
    finally { setDownloadingId(null); }
  }

  return <div className="flex flex-col gap-6">
    <section aria-labelledby="reports-title"><h2 id="reports-title" className="text-lg font-semibold text-white">Reports</h2><p className="text-supporting text-slate-400">Generate and retrieve frozen outputs from existing dataset results.</p></section>
    <Card className="max-w-3xl"><h3 className="mb-3 text-panel-title text-slate-950">Generate report</h3>
      {validDatasets.length === 0 ? <EmptyState title="No valid datasets yet" description="Upload and analyze a valid source image before generating a report." action={<Button variant="secondary" onClick={onOpenAnalysis}>Open analysis</Button>} /> : <>
        <label className="mb-1 block text-sm font-medium text-slate-700">Source dataset</label>
        <select aria-label="Report source dataset" value={selectedDatasetId} onChange={(event) => setSelectedDatasetId(event.target.value)} className="mb-3 w-full max-w-md rounded-control border border-slate-300 px-3 py-2 text-sm"><option value="">Select a dataset...</option>{validDatasets.map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.original_filename}</option>)}</select>
        <Button onClick={handleGenerate} disabled={!selectedDatasetId} loading={creating} loadingLabel="Starting report generation...">Generate report</Button>
        <p className="mt-3 text-supporting text-slate-500">PDF, JSON, CSV, and ZIP availability comes from the completed report. Only persisted analysis, calibration, terrain, screening, and measurement results are included.</p>
      </>}
    </Card>
    {error && <Notice tone="error" title="Report action failed" announce>{error} Check the selected dataset and try again.</Notice>}
    <section aria-labelledby="recent-reports-title"><h2 id="recent-reports-title" className="mb-3 text-lg font-semibold text-white">Recent reports</h2>
      {!selectedDatasetId ? <EmptyState title="Select a dataset" description="Choose a source dataset above to load its report history." /> : reports === null ? <LoadingState title="Loading report history" description="Reading report metadata only; report contents are not downloaded." /> : reports.length === 0 ? <EmptyState title="No reports generated yet" description="Run an analysis if needed, then generate a report for this dataset." action={<Button variant="secondary" onClick={onOpenAnalysis}>Open analysis</Button>} /> : <div className="flex flex-col gap-3">{reports.map((report) => <ReportRow key={report.id} report={report} creating={creating} downloadingId={downloadingId} onRetry={handleGenerate} onDownload={(kind) => handleDownload(report.id, kind)} />)}</div>}
    </section>
  </div>;
}

function ReportRow({ report, creating, downloadingId, onRetry, onDownload }: { report: Report; creating: boolean; downloadingId: string | null; onRetry: () => void; onDownload: (kind: "pdf" | "json" | "csv" | "bundle") => void }) {
  const files = [
    { kind: "pdf" as const, label: "PDF", name: "report.pdf", available: report.pdf_available, reason: undefined },
    { kind: "json" as const, label: "JSON", name: "report.json", available: report.status === "completed", reason: undefined },
    { kind: "csv" as const, label: "CSV", name: "report.csv", available: report.csv_available, reason: "No tabular measurements or screening statistics are available." },
    { kind: "bundle" as const, label: "ZIP bundle", name: "terrainx_report_bundle.zip", available: report.bundle_available, reason: undefined },
  ];
  return <article className="rounded-panel border border-slate-200 bg-white shadow-panel" aria-label="Terrain analysis report">
    <header className="flex min-w-0 flex-wrap items-start justify-between gap-2 border-b border-slate-200 px-4 py-3"><div className="min-w-0"><h3 className="text-panel-title text-slate-950">Terrain analysis report</h3><p className="truncate font-mono text-metadata text-slate-500">{report.id}</p></div><span role="status" aria-live="polite" aria-atomic="true"><Badge tone={STATUS_TONE[report.status]} dot={report.status === "generating"}>{reportStatusLabel(report.status)}</Badge></span></header>
    <div className="px-4 py-3"><dl className="grid grid-cols-1 gap-1 text-supporting sm:grid-cols-2"><div><dt className="text-slate-500">Created</dt><dd>{new Date(report.created_at).toLocaleString()}</dd></div><div><dt className="text-slate-500">Completed</dt><dd>{report.completed_at ? new Date(report.completed_at).toLocaleString() : "Not completed"}</dd></div></dl>
      {report.status === "failed" ? <Notice tone="error" title="Generation failed" announce className="mt-3">{report.error_message ?? "The report worker did not complete this report."}<div className="mt-2"><Button size="sm" variant="secondary" onClick={onRetry} disabled={creating}>Retry</Button></div></Notice> : isReportActive(report.status) ? <div role="status" aria-live="polite" aria-busy="true" className="mt-3 flex items-center gap-2 text-supporting text-slate-600"><Spinner />{report.status === "pending" ? "Queued for generation" : "Generating report from persisted results"}</div> : <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">{files.map((file) => <Button key={file.kind} aria-label={file.label} variant="secondary" className="h-auto min-w-0 flex-col items-start py-2 text-left" onClick={() => onDownload(file.kind)} disabled={!file.available} loading={downloadingId === `${report.id}:${file.kind}`} loadingLabel={`Downloading ${file.label}`} title={!file.available ? file.reason : undefined}><span aria-hidden="true">{file.label}</span><span aria-hidden="true" className="max-w-full truncate font-mono text-metadata font-normal">{file.name}</span></Button>)}</div>}
    </div>
  </article>;
}
