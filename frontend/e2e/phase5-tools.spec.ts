import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const FIXTURES = path.resolve(import.meta.dirname, "fixtures");

function uniqueEmail(): string {
  return `phase5-tools-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

async function openCalibratedWorkspace(page: Page): Promise<void> {
  await page.goto("/register");
  await page.locator('input[type="email"]').fill(uniqueEmail());
  await page.locator('input[type="password"]').fill("Phase5ToolsPass123!");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/dashboard/);
  await page.getByRole("link", { name: "Projects" }).first().click();
  const projectName = `Phase 5 Tools ${Date.now()}`;
  await page.getByPlaceholder("e.g. Coastal Flood Study").fill(projectName);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("link", { name: projectName }).click();
  await page.locator('input[type="file"]').first().setInputFiles(path.join(FIXTURES, "calibrated-source.tif"));
  await expect(page.getByText("valid", { exact: false }).first()).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "DEM reference" }).click();
  await page.locator('input[type="file"]').first().setInputFiles(path.join(FIXTURES, "calibrated-dem.tif"));
  await expect(page.getByText("valid", { exact: false }).nth(1)).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.locator("select").first().selectOption({ index: 1 });
  await page.getByRole("button", { name: "DEM reference" }).click();
  await page.locator("select").nth(1).selectOption({ index: 1 });
  await page.getByRole("button", { name: "Start Analysis" }).click();
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Terrain", exact: true }).click();
  await page.getByLabel("Dataset").selectOption({ index: 1 });
  await expect(page.getByTestId("terrain-workspace-shell")).toBeVisible({ timeout: 15_000 });
}

async function assertNoHorizontalOverflow(page: Page, label: string): Promise<void> {
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth),
    `${label} must not introduce horizontal page overflow`,
  ).toBe(false);
}

async function clickOverlay(page: Page, x: number, y: number): Promise<void> {
  const overlay = page.locator(".leaflet-image-layer").last();
  await expect(overlay).toBeVisible({ timeout: 15_000 });
  const box = await overlay.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.click(box!.x + box!.width * x, box!.y + box!.height * y);
}

test.describe("Phase 5 operational tools", () => {
  test("measure, screen, flythrough, recording/export, and responsive baselines", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1440, height: 900 });
    await openCalibratedWorkspace(page);

    await test.step("scenario 1: measurement activation, result, clear, and responsive presentation", async () => {
      await page.getByRole("button", { name: "Measure", exact: true }).click();
      const metricRow = page.getByRole("button", { name: "Metric Elevation", exact: true }).locator("xpath=..");
      await metricRow.getByRole("checkbox", { name: "Visible" }).click();
      await page.getByRole("button", { name: "Point elevation" }).click();
      await expect(page.getByRole("button", { name: "Point elevation" })).toHaveAttribute("aria-pressed", "true");
      await clickOverlay(page, 0.5, 0.5);
      await expect(page.getByRole("heading", { name: "Measurement result" })).toBeVisible({ timeout: 15_000 });
      await assertNoHorizontalOverflow(page, "1440px Measure");
      await page.screenshot({ path: "e2e/screenshots/phase5-1440-measure.png", fullPage: true });
      await page.getByRole("button", { name: "Clear measurement" }).click();
      await expect(page.getByRole("heading", { name: "Measurement result" })).toHaveCount(0);
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByRole("button", { name: "Show tools" }).click();
      await page.getByTestId("measurement-tools").scrollIntoViewIfNeeded();
      await assertNoHorizontalOverflow(page, "390px Measure");
      await page.screenshot({ path: "e2e/screenshots/phase5-390-measure.png", fullPage: true });
      await page.setViewportSize({ width: 1440, height: 900 });
    });

    await test.step("scenario 2: disaster screening lifecycle and result structure", async () => {
      await page.getByRole("button", { name: "Screen", exact: true }).click();
      await expect(page.getByText("Screening, not prediction")).toBeVisible();
      await page.getByPlaceholder("e.g. 121.0").fill("125");
      await page.getByRole("button", { name: "Run screening" }).click();
      await expect(page.getByRole("heading", { name: "Screening result" })).toBeVisible({ timeout: 15_000 });
      await expect(page.getByText("Terrain statistics")).toBeVisible({ timeout: 45_000 });
      await expect(page.getByText("Summary", { exact: true })).toBeVisible();
      await expect(page.getByText("Details", { exact: true })).toBeVisible();
      await expect(page.getByText("Actions", { exact: true })).toBeVisible();
      await assertNoHorizontalOverflow(page, "1440px Screen");
      await page.screenshot({ path: "e2e/screenshots/phase5-1440-screen.png", fullPage: true });
    });

    await test.step("scenario 3: waypoint path, playback, pause, and exact terminal state", async () => {
      await page.getByRole("button", { name: "Flythrough", exact: true }).click();
      await page.getByRole("button", { name: "Add waypoints" }).click();
      await expect(page.getByText(/loading terrain/i)).toHaveCount(0, { timeout: 20_000 });
      for (const [x, y] of [[0.3, 0.7], [0.7, 0.55], [0.35, 0.3], [0.6, 0.35], [0.7, 0.35]]) {
        const conversion = page.waitForResponse((response) => response.url().includes("/terrain/local-coordinate"));
        await clickOverlay(page, x, y);
        expect((await conversion).status()).toBe(200);
        if (await page.getByTestId("waypoint-list").locator("li").count() >= 2) break;
      }
      expect(await page.getByTestId("waypoint-list").locator("li").count()).toBeGreaterThanOrEqual(2);
      await page.getByRole("button", { name: "Stop adding waypoints" }).click();
      await page.getByRole("button", { name: "3D Terrain" }).click();
      await expect(page.locator("canvas:not([data-decorative-canvas])")).toBeVisible({ timeout: 20_000 });
      await expect(page.getByTestId("path-summary")).toContainText("Path ready", { timeout: 15_000 });
      await page.getByTestId("flythrough-path-card").scrollIntoViewIfNeeded();
      await page.screenshot({ path: "e2e/screenshots/phase5-1440-flythrough.png", fullPage: true });
      await page.getByLabel("Playback speed").selectOption("0.5");
      await page.getByRole("button", { name: "Play path" }).click();
      await expect(page.getByRole("button", { name: "Pause" })).toBeVisible();
      await page.getByRole("button", { name: "Pause" }).click();
      await expect(page.getByRole("button", { name: "Resume" })).toBeVisible();
      await page.getByRole("button", { name: "Resume" }).click();
      await expect(page.getByTestId("path-status")).toHaveAttribute("data-value", "finished", { timeout: 60_000 });
      await expect(page.getByTestId("path-progress")).toHaveAttribute("data-value", "1");
      await expect(page.getByTestId("playback-progress-text")).toHaveText("100%");
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByRole("button", { name: "Show tools" }).click();
      await page.getByTestId("flythrough-path-card").scrollIntoViewIfNeeded();
      await assertNoHorizontalOverflow(page, "390px Flythrough");
      await page.screenshot({ path: "e2e/screenshots/phase5-390-flythrough.png", fullPage: true });
      await page.setViewportSize({ width: 1440, height: 900 });
    });

    await test.step("scenario 4: recording and GLB export lifecycle", async () => {
      await page.getByRole("button", { name: "Restart" }).click();
      await page.getByRole("button", { name: "Stop" }).click();
      const recording = page.getByRole("button", { name: "Record flythrough (WebM)" });
      if (await recording.isEnabled()) {
        const videoDownload = page.waitForEvent("download", { timeout: 90_000 });
        await recording.click();
        await expect(page.getByRole("button", { name: "Stop recording" })).toBeVisible();
        const download = await videoDownload;
        expect(download.suggestedFilename()).toMatch(/\.webm$/);
        await expect(page.getByText("Recording saved.")).toBeVisible();
      } else {
        await expect(page.getByTestId("recording-unsupported")).toBeVisible();
      }
      const glbDownload = page.waitForEvent("download", { timeout: 30_000 });
      await page.getByRole("button", { name: "Export 3D mesh (GLB)" }).click();
      expect((await glbDownload).suggestedFilename()).toMatch(/\.glb$/);
      await expect(page.getByTestId("mesh-export-summary")).toBeVisible({ timeout: 10_000 });
    });

    expect(pageErrors).toEqual([]);
  });
});
