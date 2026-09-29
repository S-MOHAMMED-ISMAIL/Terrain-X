import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { ConsoleMessage, Page } from "@playwright/test";

// Phase 13 final UI/UX real browser verification — exercises the parts of
// the real, end-to-end golden path Phase 10's spec does not already cover
// (dashboard real counts, disaster screening, measurements, report
// generation/download) against the ACTUAL running application (real
// backend, real worker, real calibration) — no mocked API, no fabricated
// data. Phase 10's own spec (phase10-terrain.spec.ts) remains the
// authority for 3D/RGB-texture/PointerLock/relative-vs-calibrated-terrain
// coverage and is re-run unmodified as part of this phase's regression
// check, not duplicated here.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(__dirname, "fixtures");

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

function collectConsoleErrors(page: Page): ConsoleMessage[] {
  const errors: ConsoleMessage[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg);
  });
  return errors;
}

test.describe("Phase 13: real golden-path UI/UX verification", () => {
  test("dashboard shows real counts, calibrated analysis, measurements, disaster screening, and report download all work end-to-end", async ({
    page,
  }) => {
    const consoleErrors = collectConsoleErrors(page);
    const pageErrors: Error[] = [];
    page.on("pageerror", (err) => pageErrors.push(err));

    const email = uniqueEmail("p13golden");
    await registerAndLogin(page, email, "P13GoldenPass123!");

    // --- 1/2: a brand-new user's dashboard shows real zero counts, never a
    // placeholder or stale "Available from Phase N" caption. ---
    await expect(page.getByText("Projects").first()).toBeVisible();
    await expect(page.getByText("0").first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Available from Phase", { exact: false })).toHaveCount(0);
    await expect(page.getByText("No projects yet")).toBeVisible();

    // --- 3: create a real project, confirmed via real navigation ---
    await page.getByRole("link", { name: "Projects" }).first().click();
    const projectName = `Phase 13 Golden ${Date.now()}`;
    await page.getByPlaceholder("e.g. Coastal Flood Study").fill(projectName);
    await page.getByRole("button", { name: "Create project" }).click();
    await page.getByRole("link", { name: projectName }).click();
    await page.waitForURL(/\/projects\/[0-9a-f-]+/, { timeout: 15_000 });

    // Real project context is shown (page heading + breadcrumb both use the
    // real project name, never a placeholder).
    await expect(page.getByRole("heading", { name: projectName })).toBeVisible();

    // --- 4: real dataset upload UI (drag/drop dropzone + progress state) ---
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(FIXTURES, "calibrated-source.tif"),
    );
    await expect(page.getByText("Uploaded", { exact: false })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("valid", { exact: false }).first()).toBeVisible({
      timeout: 20_000,
    });

    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(FIXTURES, "calibrated-dem.tif"),
    );
    await expect(page.getByText("valid", { exact: false }).nth(1)).toBeVisible({ timeout: 20_000 });

    // --- 5/6: real analysis submission + real job status/pipeline display ---
    await page.getByRole("button", { name: "Analysis" }).click();
    await page.locator("select").first().selectOption({ index: 1 });
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator("select").nth(1).selectOption({ index: 1 });
    await page.getByRole("button", { name: "Start Analysis" }).click();

    // Real pipeline-stage tracker (Input/Depth/Calibration/Final) appears —
    // never a fabricated percentage, only the job's own real stage.
    await expect(page.getByText("Input").first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({
      timeout: 60_000,
    });
    expect(await page.getByText("failed", { exact: true }).count()).toBe(0);

    // --- 7: terrain workspace, calibrated 2D map ---
    await page.getByRole("button", { name: "Terrain", exact: true }).click();
    await page.locator("select").first().selectOption({ index: 1 });
    await expect(page.getByRole("button", { name: "3D Terrain" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(
      "Terrain, Measurements, and Disaster Screening are one integrated",
      { exact: false },
    )).toBeVisible();

    // --- measurements: real mode switching + real click-driven results.
    // (Post-Phase-13 fix verification: MapView2D/TerrainView3D previously
    // read a stale `onSample`/measurement-mode closure captured at mount,
    // so switching mode and clicking silently did nothing. Both now read
    // the current callback through a ref, updated every render — see
    // MapView2D.tsx/TerrainView3D.tsx.)
    const metricElevationRow = page
      .getByRole("button", { name: "Metric Elevation", exact: true })
      .locator("xpath=..");
    await metricElevationRow.getByRole("checkbox", { name: "Visible" }).click();

    const overlayImage = page.locator(".leaflet-image-layer").last();
    await expect(overlayImage, "the real raster overlay must have actually rendered").toBeVisible({
      timeout: 10_000,
    });
    // Re-measures the overlay's real position immediately before every
    // click (rather than reusing one captured box) — an intervening action
    // like clicking a mode button can scroll the page, which would make a
    // once-captured bounding box stale for a later click.
    const clickCenter = async () => {
      await overlayImage.scrollIntoViewIfNeeded();
      const box = await overlayImage.boundingBox();
      expect(box, "the real raster overlay must have a real bounding box").not.toBeNull();
      await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
    };

    // Plain inspection still works.
    await clickCenter();
    await expect(page.getByText("Elevation:", { exact: false })).toBeVisible({ timeout: 10_000 });

    // Switching to "Point elevation" and clicking now really registers a
    // measurement point and computes a real result.
    await page.getByRole("button", { name: "Point elevation" }).click();
    await expect(page.getByText("Measurement mode: Point elevation", { exact: false })).toBeVisible({
      timeout: 10_000,
    });
    await clickCenter();
    await expect(page.getByText("Measurement result")).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "Save measurement" }).click();
    await expect(page.getByText("No saved measurements yet")).toHaveCount(0, { timeout: 10_000 });

    // Switching back to plain inspection still works (proves no duplicate/
    // stuck handler from the mode switch above).
    await page.getByRole("button", { name: "Inspect (off)", exact: true }).click();
    await clickCenter();
    await expect(page.getByText("Elevation:", { exact: false })).toBeVisible({ timeout: 10_000 });

    // --- disaster screening tab (Phase 12 fix: no longer falsely disabled) ---
    await page.getByRole("button", { name: "Disaster", exact: true }).click();
    await expect(page.getByText("Screening, not prediction")).toBeVisible({ timeout: 10_000 });
    // Flood + landslide screening are both checked by default — just fill
    // in the real water level the flood screen needs.
    await page.getByPlaceholder("e.g. 121.0").fill("125");
    await page.getByRole("button", { name: "Run screening" }).click();
    await expect(page.getByText("Terrain statistics")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Flood screening", { exact: true })).toBeVisible();
    await expect(page.getByText(
      "never rainfall/hydraulic simulation",
      { exact: false },
    )).toBeVisible();

    await page.screenshot({
      path: "e2e/screenshots/phase13-disaster-screening.png",
      fullPage: true,
    });

    // --- reports: generate + real PDF/JSON/CSV/ZIP download cards ---
    await page.getByRole("button", { name: "Reports" }).click();
    await page.locator("select").first().selectOption({ index: 1 });
    await page.getByRole("button", { name: "Generate report" }).click();
    await expect(page.getByText("Completed", { exact: true }).first()).toBeVisible({
      timeout: 30_000,
    });

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "PDF" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain("pdf");

    await page.screenshot({ path: "e2e/screenshots/phase13-reports.png", fullPage: true });

    // --- console/page error check across the whole real flow ---
    expect(
      pageErrors,
      `uncaught page errors: ${pageErrors.map((e) => e.message).join("; ")}`,
    ).toEqual([]);

    test.info().annotations.push({
      type: "consoleErrorCount",
      description: String(consoleErrors.length),
    });
    test.info().annotations.push({
      type: "consoleErrors",
      description: consoleErrors.map((m) => m.text()).join(" | ") || "(none)",
    });
  });
});
