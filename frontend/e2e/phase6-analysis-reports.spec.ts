import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const FIXTURES = path.resolve(import.meta.dirname, "fixtures");

async function registerAndOpenProject(page: Page): Promise<void> {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
  await page.goto("/register");
  await page.locator('input[type="email"]').fill(`phase6-${stamp}@example.com`);
  await page.locator('input[type="password"]').fill("Phase6AnalysisPass123!");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/dashboard/);
  await page.getByRole("link", { name: "Projects" }).first().click();
  const projectName = `Phase 6 Analysis ${stamp}`;
  await page.getByPlaceholder("e.g. Coastal Flood Study").fill(projectName);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("link", { name: projectName }).click();
  await page.waitForURL(/\/projects\/[0-9a-f-]+/);
}

async function upload(page: Page, filename: string, role?: "DEM reference"): Promise<void> {
  if (role) await page.getByRole("button", { name: role }).click();
  const before = await page.getByText("valid", { exact: false }).count();
  await page.locator('input[type="file"]').first().setInputFiles(path.join(FIXTURES, filename));
  await expect(page.getByText("valid", { exact: false })).toHaveCount(before + 1, { timeout: 20_000 });
}

async function expectNoOverflow(page: Page, width: number, height: number): Promise<void> {
  await page.setViewportSize({ width, height });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
}

test.describe("Phase 6 analysis and reports experience", () => {
  test("real analysis products, diagnostics, workspace transition, report lifecycle, download, and responsive layouts", async ({ page }) => {
    test.setTimeout(150_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await registerAndOpenProject(page);
    await upload(page, "calibrated-source.tif");
    await upload(page, "calibrated-dem.tif", "DEM reference");

    await test.step("1. Analysis overview", async () => {
      await page.getByRole("button", { name: "Analysis" }).click();
      await expect(page.getByRole("heading", { name: "Available analysis" })).toBeVisible();
      await expect(page.getByText("Relative depth", { exact: true })).toBeVisible();
      await expect(page.getByText("DTM and nDSM", { exact: true })).toBeVisible();
      await page.locator("select").first().selectOption({ index: 1 });
      await page.getByRole("button", { name: "DEM reference" }).click();
      await page.locator("select").nth(1).selectOption({ index: 1 });
      await page.getByRole("button", { name: "Start Analysis" }).click();
      await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: "e2e/screenshots/phase6-analysis-1440.png", fullPage: true });
    });

    const jobCard = page.getByRole("article", { name: /Analysis job/ }).first();
    await test.step("2. DTM and nDSM state presentation", async () => {
      await expect(jobCard.getByText("DTM", { exact: true })).toBeVisible();
      await expect(jobCard.getByText("nDSM", { exact: true })).toBeVisible();
      await expect(jobCard.getByRole("button", { name: "Download DTM (estimate)" })).toBeEnabled();
      await expect(jobCard.getByRole("button", { name: "Download nDSM (estimate)" })).toBeEnabled();
      await expect(jobCard.getByText(/DSM minus estimated DTM/)).toBeVisible();
    });

    await test.step("3. Calibration diagnostics and workspace transition", async () => {
      await jobCard.getByText("Calibration and diagnostics", { exact: true }).click();
      await expect(jobCard.getByRole("heading", { name: "In-sample fit" })).toBeVisible();
      await expect(jobCard.getByRole("heading", { name: "Held-out validation" })).toBeVisible();
      await expect(jobCard.getByText(/Residual = Predicted - Reference/)).toBeVisible();
      await jobCard.getByRole("button", { name: "Open in workspace" }).click();
      const engineHost = page.getByTestId("terrain-engine-host");
      await expect(page.getByTestId("terrain-workspace-shell")).toBeVisible({ timeout: 15_000 });
      await engineHost.evaluate((element) => element.setAttribute("data-phase6-persistent", "true"));
      await page.getByRole("button", { name: "Analysis" }).click();
      await expect(page.getByRole("heading", { name: "Active and recent jobs" })).toBeVisible();
      await expect(engineHost).toHaveAttribute("data-phase6-persistent", "true");
    });

    await test.step("4. Report lifecycle", async () => {
      await page.getByRole("button", { name: "Reports" }).click();
      await page.getByRole("combobox", { name: "Report source dataset" }).selectOption({ index: 1 });
      await page.getByRole("button", { name: "Generate report" }).click();
      const reportCard = page.getByRole("article", { name: "Terrain analysis report" }).first();
      await expect(reportCard.locator("header").getByText("Completed", { exact: true })).toBeVisible({ timeout: 30_000 });
      await expect(page.getByRole("button", { name: "PDF" })).toBeEnabled();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: "e2e/screenshots/phase6-reports-1440.png", fullPage: true });
    });

    await test.step("5. Report download", async () => {
      const downloadPromise = page.waitForEvent("download");
      await page.getByRole("button", { name: "PDF" }).click();
      const download = await downloadPromise;
      expect(download.suggestedFilename().toLowerCase()).toContain("pdf");
    });

    await test.step("6. Responsive Analysis and Reports", async () => {
      await page.getByRole("button", { name: "Analysis" }).click();
      await expectNoOverflow(page, 1024, 768);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: "e2e/screenshots/phase6-analysis-1024.png", fullPage: true });
      await expectNoOverflow(page, 768, 900);
      await expectNoOverflow(page, 390, 844);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: "e2e/screenshots/phase6-analysis-390.png", fullPage: true });
      await page.getByRole("button", { name: "Reports" }).click();
      await expectNoOverflow(page, 390, 844);
      await page.getByRole("combobox", { name: "Report source dataset" }).selectOption({ index: 1 });
      await expect(page.getByText("Completed", { exact: true }).first()).toBeVisible();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: "e2e/screenshots/phase6-reports-390.png", fullPage: true });
    });
  });
});
