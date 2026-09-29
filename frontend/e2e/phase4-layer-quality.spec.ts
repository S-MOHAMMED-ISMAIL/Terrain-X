import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const FIXTURES = path.resolve(import.meta.dirname, "fixtures");

function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

async function createWorkspace(page: Page, projectName: string): Promise<void> {
  await page.goto("/register");
  await page.locator('input[type="email"]').fill(uniqueEmail("phase4"));
  await page.locator('input[type="password"]').fill("Phase4QualityPass123!");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/dashboard/);
  await page.getByRole("link", { name: "Projects" }).first().click();
  await page.getByPlaceholder("e.g. Coastal Flood Study").fill(projectName);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("link", { name: projectName }).click();
}

async function upload(page: Page, filename: string, role?: "DEM reference"): Promise<void> {
  if (role) await page.getByRole("button", { name: role }).click();
  const before = await page.getByText("valid", { exact: false }).count();
  await page.locator('input[type="file"]').first().setInputFiles(path.join(FIXTURES, filename));
  await expect(page.getByText("valid", { exact: false })).toHaveCount(before + 1, { timeout: 20_000 });
}

async function runAnalysis(page: Page, withReference: boolean): Promise<void> {
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.locator("select").first().selectOption({ index: 1 });
  if (withReference) {
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator("select").nth(1).selectOption({ index: 1 });
  }
  await page.getByRole("button", { name: "Start Analysis" }).click();
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
}

async function openTerrain(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Terrain", exact: true }).click();
  await page.getByLabel("Dataset").selectOption({ index: 1 });
  await expect(page.getByTestId("terrain-workspace-shell")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".leaflet-container")).toBeVisible({ timeout: 15_000 });
}

async function expectNoOverflow(page: Page, width: number, height: number): Promise<void> {
  await page.setViewportSize({ width, height });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
}

test.describe("Phase 4 layer system and data quality", () => {
  test("metric workspace exposes truthful quality, layer details, diagnostics, and residual access", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await createWorkspace(page, `Phase 4 Metric ${Date.now()}`);
    await upload(page, "calibrated-source.tif");
    await upload(page, "calibrated-dem.tif", "DEM reference");
    await runAnalysis(page, true);
    await openTerrain(page);

    const shell = page.getByTestId("terrain-workspace-shell");
    await expect(shell.getByText("Metric", { exact: true })).toBeVisible();
    await expect(shell.getByText("metre", { exact: true }).first()).toBeVisible();
    await expect(shell.getByText("Passed", { exact: true }).first()).toBeVisible();

    await shell.getByText("Quality details", { exact: true }).click();
    await expect(
      shell.getByRole("region", { name: "Data quality" }).getByText(/not an independent accuracy guarantee/),
    ).toBeVisible();

    const metricLayer = page.getByRole("button", { name: "Metric Elevation", exact: true });
    await metricLayer.click();
    await expect(metricLayer).toHaveAttribute("aria-pressed", "true");
    const metricRow = metricLayer.locator("xpath=..");
    const visible = metricRow.getByRole("checkbox", { name: "Visible" });
    await visible.check();
    await expect(visible).toBeChecked();
    await expect(metricLayer).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("heading", { name: "Metric Elevation" })).toBeVisible();
    await expect(page.getByText(/NoData.*not zero/)).toBeVisible();
    await expect(page.locator(".leaflet-image-layer")).toHaveCount(2, { timeout: 15_000 });

    await page.getByText("Show calibration diagnostics", { exact: true }).click();
    await expect(page.getByRole("heading", { name: "In-sample fit" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Held-out validation" })).toBeVisible();
    await expect(page.getByText("Fit diagnostics over the calibration samples; not validation.")).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "Show calibration residuals" })).toBeVisible();

    const host = page.getByTestId("terrain-engine-host");
    await host.evaluate((element) => element.setAttribute("data-phase4-persistent", "true"));
    await page.waitForLoadState("networkidle");
    let previewRequests = 0;
    const previewUrls: string[] = [];
    page.on("request", (request) => {
      if (/\/visualization\/(map-preview|preview)$/.test(new URL(request.url()).pathname)) {
        previewRequests++;
        previewUrls.push(request.url());
      }
    });
    const beforePresentationChanges = previewRequests;
    await page.getByRole("button", { name: "Hide tools" }).click();
    await page.getByRole("button", { name: "Show tools" }).click();
    await page.getByRole("button", { name: "Hide results" }).click();
    await page.getByRole("button", { name: "Show results" }).click();
    await expect(host).toHaveAttribute("data-phase4-persistent", "true");
    expect(previewRequests, `panel-only preview requests: ${previewUrls.join(", ")}`).toBe(beforePresentationChanges);

    await expectNoOverflow(page, 1440, 900);
    await expectNoOverflow(page, 1024, 768);
    await expectNoOverflow(page, 768, 900);
    await expectNoOverflow(page, 390, 844);
    await page.getByRole("button", { name: "Show layers" }).click();
    await expect(metricLayer).toBeVisible();
    await page.getByRole("button", { name: "Hide layers" }).click();
    await page.getByRole("button", { name: "Show layers" }).click();
    await expect(metricLayer).toHaveAttribute("aria-pressed", "true");
  });

  test("rejected calibration remains relative and explains unavailable metric output", async ({ page }) => {
    await createWorkspace(page, `Phase 4 Rejected ${Date.now()}`);
    await upload(page, "uncalibrated-source.jpg");
    await upload(page, "calibrated-dem.tif", "DEM reference");
    await runAnalysis(page, true);
    await openTerrain(page);

    const shell = page.getByTestId("terrain-workspace-shell");
    await expect(shell.getByText("Relative", { exact: true })).toBeVisible();
    await expect(shell.getByText("Unitless", { exact: true }).first()).toBeVisible();
    await expect(shell.getByText(/Rejected|Failed/, { exact: true }).first()).toBeVisible();
    await page.getByText("Show calibration diagnostics", { exact: true }).click();
    await expect(page.getByText("Relative output remains available")).toBeVisible();
    await expect(page.getByText("Relative depth is usable as a unitless visualization. It is not metric elevation.")).toBeVisible();

    const metricLayer = page.getByRole("button", { name: "Metric Elevation", exact: true });
    await expect(metricLayer).toBeDisabled();
    await expect(metricLayer.locator("xpath=../..")).toContainText(/calibration failed|quality gate/i);
    await expect(page.getByRole("checkbox", { name: "Show calibration residuals" })).toHaveCount(0);
    await expectNoOverflow(page, 390, 844);
  });
});
