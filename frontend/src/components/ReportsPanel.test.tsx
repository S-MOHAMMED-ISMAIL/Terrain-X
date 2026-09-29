// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Dataset, Report } from "@/api/types";
import { ReportsPanel } from "./ReportsPanel";

const apiMock = vi.hoisted(() => ({
  listReports: vi.fn(),
  createReport: vi.fn(),
  downloadReportPdf: vi.fn(),
  downloadReportJson: vi.fn(),
  downloadReportCsv: vi.fn(),
  downloadReportBundle: vi.fn(),
}));

vi.mock("@/api/client", () => ({ ApiError: class ApiError extends Error {}, api: apiMock }));

const dataset: Dataset = {
  id: "dataset-1", project_id: "project-1", original_filename: "terrain.tif",
  file_type: "tif", mime_type: "image/tiff", file_size_bytes: 1024,
  role: "source_image", status: "valid", validation_error: null, width: 64,
  height: 64, bands: 1, is_georeferenced: true, crs: "EPSG:32643", bbox: null,
  gcp_crs: null, gcp_point_count: null, created_at: "2026-09-29T10:00:00Z",
  updated_at: "2026-09-29T10:00:00Z",
};

function report(status: Report["status"], overrides: Partial<Report> = {}): Report {
  return {
    id: `report-${status}`, project_id: "project-1", dataset_id: dataset.id,
    user_id: "user-1", status, error_message: null,
    pdf_available: status === "completed", csv_available: status === "completed",
    bundle_available: status === "completed", created_at: "2026-09-29T10:00:00Z",
    completed_at: status === "completed" ? "2026-09-29T10:01:00Z" : null,
    updated_at: "2026-09-29T10:01:00Z", ...overrides,
  };
}

async function selectDataset() {
  await userEvent.setup().selectOptions(screen.getByRole("combobox", { name: "Report source dataset" }), dataset.id);
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.listReports.mockResolvedValue([]);
  apiMock.createReport.mockResolvedValue(undefined);
});
afterEach(cleanup);

describe("ReportsPanel lifecycle", () => {
  it("shows the no-dataset recovery path", () => {
    const onOpenAnalysis = vi.fn();
    render(<ReportsPanel projectId="project-1" datasets={[]} onOpenAnalysis={onOpenAnalysis} />);
    expect(screen.getByText("No valid datasets yet")).toBeVisible();
    expect(screen.getByRole("button", { name: "Open analysis" })).toBeEnabled();
  });

  it.each([
    ["pending", "Queued", "Queued for generation"],
    ["generating", "Generating…", "Generating report from persisted results"],
  ] as const)("announces the %s state", async (status, label, message) => {
    apiMock.listReports.mockResolvedValue([report(status)]);
    render(<ReportsPanel projectId="project-1" datasets={[dataset]} onOpenAnalysis={vi.fn()} />);
    await selectDataset();
    const card = await screen.findByRole("article", { name: "Terrain analysis report" });
    expect(within(card).getByText(label, { exact: true })).toBeVisible();
    expect(within(card).getAllByRole("status").some((node) => node.getAttribute("aria-busy") === "true")).toBe(true);
    expect(within(card).getByText(message)).toBeVisible();
  });

  it("shows failure detail and retries through the existing create endpoint", async () => {
    apiMock.listReports.mockResolvedValue([report("failed", { error_message: "Worker timeout" })]);
    render(<ReportsPanel projectId="project-1" datasets={[dataset]} onOpenAnalysis={vi.fn()} />);
    await selectDataset();
    expect(await screen.findByText("Worker timeout", { exact: false })).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(apiMock.createReport).toHaveBeenCalledWith("project-1", dataset.id));
  });

  it("exposes completed files with stable accessible names", async () => {
    apiMock.listReports.mockResolvedValue([report("completed")]);
    render(<ReportsPanel projectId="project-1" datasets={[dataset]} onOpenAnalysis={vi.fn()} />);
    await selectDataset();
    const card = await screen.findByRole("article", { name: "Terrain analysis report" });
    expect(within(card).getAllByText("Completed", { exact: true })).not.toHaveLength(0);
    expect(screen.getByRole("button", { name: "PDF" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "JSON" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "CSV" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "ZIP bundle" })).toBeEnabled();
    await userEvent.setup().click(screen.getByRole("button", { name: "PDF" }));
    expect(apiMock.downloadReportPdf).toHaveBeenCalledWith("project-1", "report-completed");
  });

  it("keeps unavailable optional files disabled with an explanation", async () => {
    apiMock.listReports.mockResolvedValue([report("completed", { csv_available: false })]);
    render(<ReportsPanel projectId="project-1" datasets={[dataset]} onOpenAnalysis={vi.fn()} />);
    await selectDataset();
    const csv = await screen.findByRole("button", { name: "CSV" });
    expect(csv).toBeDisabled();
    expect(csv).toHaveAttribute("title", "No tabular measurements or screening statistics are available.");
  });
});
