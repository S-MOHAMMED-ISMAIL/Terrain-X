import { describe, expect, it } from "vitest";
import type { ReportStatus } from "@/api/types";
import { isReportActive, reportStatusLabel } from "./reportFormat";

describe("isReportActive", () => {
  it("treats pending and generating as active (still polling)", () => {
    expect(isReportActive("pending")).toBe(true);
    expect(isReportActive("generating")).toBe(true);
  });

  it("treats completed and failed as terminal (stop polling)", () => {
    expect(isReportActive("completed")).toBe(false);
    expect(isReportActive("failed")).toBe(false);
  });
});

describe("reportStatusLabel", () => {
  it("provides a real, distinct label for every status", () => {
    const statuses: ReportStatus[] = ["pending", "generating", "completed", "failed"];
    const labels = statuses.map(reportStatusLabel);
    expect(new Set(labels).size).toBe(statuses.length);
  });

  it("labels 'generating' with an in-progress indicator, never as if already done", () => {
    expect(reportStatusLabel("generating")).toContain("…");
  });

  it("never claims success for a failed report", () => {
    expect(reportStatusLabel("failed").toLowerCase()).not.toContain("complet");
  });
});
