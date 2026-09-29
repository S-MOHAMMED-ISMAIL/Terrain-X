import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { ConsoleMessage, Page, Response } from "@playwright/test";

// P1-5 acceptance: calibration residuals at calibration sample locations,
// against the ACTUAL running application (real backend, real worker, real
// calibration) — no mocked API, no fabricated data. Every value checked in
// the browser is compared with what the API returns, and the API's held-out
// values are checked against the P1-2 cross-validation stored on the job
// (each fold's own a/b), so a recomputation from the production fit would
// be caught.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(__dirname, "fixtures");
const UNITS = "units of the calibration reference";

interface ResidualProps {
  sample_index: number;
  row: number;
  col: number;
  relative_depth: number;
  reference_elevation: number;
  predicted_heldout: number;
  residual_heldout: number;
  predicted_fit: number;
  residual_fit: number;
  inlier_in_production_fit: boolean;
  fold_id: number;
  block_id: number | null;
  gcp_index: number | null;
}

interface ResidualsBody {
  artifact_id: string;
  analysis_job_id: string;
  summary: { reference_type: string; total_candidate_samples: number };
  feature_collection: { features: { properties: ResidualProps }[] };
}

function uniqueEmail(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.floor(Math.random() * 1e6)}@example.com`;
}

async function registerAndLogin(page: Page, email: string, password: string) {
  await page.goto("/register");
  await page.locator('input[type="email"]').fill(email);
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/dashboard/, { timeout: 15_000 });
}

async function createProject(page: Page, name: string): Promise<void> {
  await page.goto("/projects");
  await page.getByPlaceholder("e.g. Coastal Flood Study").fill(name);
  await page.getByRole("button", { name: "Create project" }).click();
  // Let the workspace's initial dataset-list fetch finish before any upload,
  // so the uploaded dataset is never listed twice (an existing race between
  // that fetch and the upload callback, outside P1-5's scope).
  const initialDatasets = page.waitForResponse(
    (r) => /\/projects\/[0-9a-f-]+\/datasets$/.test(new URL(r.url()).pathname) &&
      r.request().method() === "GET",
    { timeout: 15_000 },
  );
  await page.getByRole("link", { name }).click();
  await page.waitForURL(/\/projects\/[0-9a-f-]+/, { timeout: 15_000 });
  await initialDatasets;
}

async function runAnalysis(page: Page, withDemReference: boolean): Promise<void> {
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.locator("select").first().selectOption({ index: 1 });
  if (withDemReference) {
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator("select").nth(1).selectOption({ index: 1 });
  }
  await page.getByRole("button", { name: "Start Analysis" }).click();
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({
    timeout: 60_000,
  });
  expect(await page.getByText("failed", { exact: true }).count()).toBe(0);
}

// Same formatting rule as calibrationResiduals.ts (signed, 4 decimals).
function signed(value: number): string {
  const text = value.toFixed(4);
  return value > 0 ? `+${text}` : text;
}

function watchErrors(page: Page): { console: ConsoleMessage[]; page: Error[] } {
  const errors = { console: [] as ConsoleMessage[], page: [] as Error[] };
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.console.push(msg);
  });
  page.on("pageerror", (err) => errors.page.push(err));
  return errors;
}

function expectNoErrors(errors: { console: ConsoleMessage[]; page: Error[] }) {
  expect(errors.page, `uncaught page errors: ${errors.page.map((e) => e.message).join("; ")}`).toEqual(
    [],
  );
  expect(errors.console.map((m) => m.text()), "console errors").toEqual([]);
}

test.describe("P1-5: calibration residuals", () => {
  test("calibrated DEM job shows held-out residual points that match the API and the stored P1-2 cross-validation", async ({
    page,
  }) => {
    const errors = watchErrors(page);
    await registerAndLogin(page, uniqueEmail("p15resid"), "P15ResidPass123!");
    await createProject(page, `P1-5 Residuals ${Date.now()}`);

    // --- deterministic EPSG:32633 calibrated fixture ---
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(FIXTURES, "calibrated-source.tif"),
    );
    await expect(page.getByText("valid", { exact: false }).first()).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(FIXTURES, "calibrated-dem.tif"),
    );
    await expect(page.getByText("valid", { exact: false }).nth(1)).toBeVisible({ timeout: 20_000 });
    await runAnalysis(page, true);

    // --- Terrain: the workspace fetches the stored residuals ---
    await page.getByRole("button", { name: "Terrain", exact: true }).click();
    const residualsResponsePromise = page.waitForResponse(
      (r: Response) => r.url().includes("/calibration-residuals") && r.request().method() === "GET",
      { timeout: 20_000 },
    );
    await page.locator("select").first().selectOption({ index: 1 });
    const residualsResponse = await residualsResponsePromise;
    expect(residualsResponse.status()).toBe(200);
    const body = (await residualsResponse.json()) as ResidualsBody;
    const features = body.feature_collection.features.map((f) => f.properties);
    expect(body.summary.reference_type).toBe("dem");
    expect(features.length).toBeGreaterThan(0);

    // --- the API's held-out values ARE the stored P1-2 CV predictions ---
    const authorization = (await residualsResponse.request().allHeaders())["authorization"];
    const apiRoot = residualsResponse.url().split("/projects/")[0];
    const projectId = residualsResponse.url().split("/projects/")[1].split("/")[0];
    const jobResponse = await page.request.get(
      `${apiRoot}/projects/${projectId}/analysis/${body.analysis_job_id}`,
      { headers: { Authorization: authorization } },
    );
    expect(jobResponse.status()).toBe(200);
    const job = await jobResponse.json();
    expect(job.calibration_status).toBe("calibrated");
    expect(job.calibration_metadata.calibration_residuals_artifact_id).toBe(body.artifact_id);
    const cv = job.calibration_metadata.cross_validation;
    expect(cv.method).toBe("leave_one_spatial_block_out");
    expect(features.length).toBe(cv.heldout_sample_count);
    const folds = new Map<number, { scale: number; offset: number }>(
      cv.folds.map((f: { fold_id: number; scale: number; offset: number }) => [f.fold_id, f]),
    );
    for (const p of features) {
      const fold = folds.get(p.fold_id)!;
      expect(p.predicted_heldout).toBe(fold.scale * p.relative_depth + fold.offset);
      expect(p.residual_heldout).toBe(p.predicted_heldout - p.reference_elevation);
    }
    const meanHeldout = features.reduce((sum, p) => sum + p.residual_heldout, 0) / features.length;
    expect(meanHeldout).toBeCloseTo(cv.heldout_bias, 10);

    // --- residual card: held-out is the default view, points hidden until enabled ---
    const card = page.getByTestId("calibration-residuals-card");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("residual-kind-label")).toHaveText(
      "Held-out residual (validation view)",
    );
    await expect(card).toContainText(UNITS);
    await expect(card).toContainText("not a threshold");
    await expect(page.getByTestId("residual-sample-count")).toContainText(
      `${features.length} of ${body.summary.total_candidate_samples} candidate samples valid`,
    );
    await expect(page.locator("path.residual-marker")).toHaveCount(0);

    await page.getByRole("checkbox", { name: "Show calibration residuals" }).check();

    // --- marker count matches the API data ---
    await expect(page.locator("path.residual-marker")).toHaveCount(features.length, {
      timeout: 10_000,
    });
    const maxAbs = Math.max(...features.map((p) => Math.abs(p.residual_heldout)));
    await expect(page.getByTestId("residual-legend-values")).toContainText(signed(maxAbs));

    // --- tooltip matches the API feature values ---
    const inliers = features.filter((p) => p.inlier_in_production_fit);
    const target = inliers[Math.floor(inliers.length / 2)];
    const marker = page.locator(`path.residual-sample-${target.sample_index}`);
    await marker.scrollIntoViewIfNeeded();
    await marker.click();
    const tooltip = page.locator(`[data-testid="residual-tooltip"][data-sample-index="${target.sample_index}"]`);
    await expect(tooltip).toBeVisible({ timeout: 5_000 });
    expect(await tooltip.locator("p").allInnerTexts()).toEqual([
      `Held-out residual: ${signed(target.residual_heldout)} (${UNITS})`,
      `Held-out prediction: ${target.predicted_heldout.toFixed(4)}`,
      `Reference elevation: ${target.reference_elevation.toFixed(4)}`,
      `Source pixel (row, col): ${target.row}, ${target.col}`,
      `Spatial block ${target.block_id} (held out as a block)`,
    ]);

    // --- fit view: clearly labelled secondary, same marker shows the fit residual ---
    await page.keyboard.press("Escape");
    await page.getByRole("radio", { name: "Fit (in-sample)" }).click();
    await expect(page.getByTestId("residual-kind-label")).toHaveText(
      "In-sample fit residual — not validation",
    );
    await expect(page.locator("path.residual-marker")).toHaveCount(features.length);
    await page.locator(`path.residual-sample-${target.sample_index}`).click();
    const fitTooltip = page.locator(
      `[data-testid="residual-tooltip"][data-sample-index="${target.sample_index}"]`,
    );
    await expect(fitTooltip.locator("p").first()).toHaveText(
      `Fit (in-sample) residual: ${signed(target.residual_fit)} (${UNITS})`,
    );

    // --- measurement mode: markers stop taking clicks, measurements still work ---
    await page.locator(".leaflet-popup-close-button").click();
    await expect(page.locator(".leaflet-popup")).toHaveCount(0);
    // Point elevation samples a visible scientific layer (existing behaviour).
    await page
      .getByRole("button", { name: "Metric Elevation", exact: true })
      .locator("xpath=..")
      .getByRole("checkbox", { name: "Visible" })
      .click();
    await page.getByRole("button", { name: "Point elevation" }).click();
    const overlay = page.locator(".leaflet-image-layer").last();
    await expect(overlay).toBeVisible();
    await overlay.scrollIntoViewIfNeeded();
    const box = (await overlay.boundingBox())!;
    await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
    await expect(page.getByText("Measurement result")).toBeVisible({ timeout: 15_000 });

    await page.screenshot({ path: "e2e/screenshots/p15-calibration-residuals.png", fullPage: true });
    expectNoErrors(errors);
  });

  test("uncalibrated job shows the unavailable reason and no residual points", async ({ page }) => {
    const errors = watchErrors(page);
    await registerAndLogin(page, uniqueEmail("p15none"), "P15NonePass123!");
    await createProject(page, `P1-5 Uncalibrated ${Date.now()}`);
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(FIXTURES, "uncalibrated-source.jpg"),
    );
    await expect(page.getByText("valid", { exact: false }).first()).toBeVisible({ timeout: 20_000 });
    await runAnalysis(page, false);

    let residualRequests = 0;
    page.on("request", (req) => {
      if (req.url().includes("/calibration-residuals")) residualRequests++;
    });
    await page.getByRole("button", { name: "Terrain", exact: true }).click();
    await page.locator("select").first().selectOption({ index: 1 });
    await expect(page.getByRole("button", { name: "3D Terrain" })).toBeVisible({ timeout: 15_000 });

    const card = page.getByTestId("calibration-residuals-card");
    await expect(card).toContainText(
      "No calibration reference (DEM/GCP) was used for this analysis job.",
    );
    await expect(page.getByRole("checkbox", { name: "Show calibration residuals" })).toHaveCount(0);
    await expect(page.locator("path.residual-marker")).toHaveCount(0);
    expect(residualRequests).toBe(0);
    expectNoErrors(errors);
  });
});
