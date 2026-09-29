import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { ConsoleMessage, Page } from "@playwright/test";

// P1-4 acceptance: slope-at-point against the ACTUAL running application
// (real backend, real worker, real calibration, real disaster screening) —
// no mocked API, no fabricated data. The value shown in the UI must be the
// value stored in the slope raster at the pixel the backend resolved: the
// spec downloads that raster through the real artifact-download endpoint
// and reads the pixel independently with rasterio, rather than trusting
// the slope endpoint's own reader.

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

/** Reads band 1 of a GeoTIFF at (row, col) with rasterio, independently of
 * the backend's measurement code. Returns null for a NoData pixel. */
function readRasterValue(tifPath: string, row: number, col: number): number | null {
  const script = [
    "import json, sys, rasterio",
    "with rasterio.open(sys.argv[1]) as src:",
    "    v = src.read(1)[int(sys.argv[2]), int(sys.argv[3])]",
    "    nd = src.nodata",
    "    print(json.dumps(None if (nd is not None and v == nd) else float(v)))",
  ].join("\n");
  const out = execFileSync("python", ["-c", script, tifPath, String(row), String(col)], {
    encoding: "utf-8",
  });
  return JSON.parse(out.trim()) as number | null;
}

test.describe("P1-4: slope at point", () => {
  test("slope-at-point shows the authoritative slope raster value, saves to history, and other measurement modes still work", async ({
    page,
  }) => {
    const consoleErrors: ConsoleMessage[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg);
    });
    const pageErrors: Error[] = [];
    page.on("pageerror", (err) => pageErrors.push(err));

    await registerAndLogin(page, uniqueEmail("p14slope"), "P14SlopePass123!");

    // --- 1: deterministic EPSG:32633 calibrated fixture ---
    await page.getByRole("link", { name: "Projects" }).first().click();
    const projectName = `P1-4 Slope ${Date.now()}`;
    await page.getByPlaceholder("e.g. Coastal Flood Study").fill(projectName);
    await page.getByRole("button", { name: "Create project" }).click();
    await page.getByRole("link", { name: projectName }).click();
    await page.waitForURL(/\/projects\/[0-9a-f-]+/, { timeout: 15_000 });

    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(FIXTURES, "calibrated-source.tif"),
    );
    await expect(page.getByText("valid", { exact: false }).first()).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(FIXTURES, "calibrated-dem.tif"),
    );
    await expect(page.getByText("valid", { exact: false }).nth(1)).toBeVisible({ timeout: 20_000 });

    // --- 2: calibrated analysis ---
    await page.getByRole("button", { name: "Analysis" }).click();
    await page.locator("select").first().selectOption({ index: 1 });
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator("select").nth(1).selectOption({ index: 1 });
    await page.getByRole("button", { name: "Start Analysis" }).click();
    await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({
      timeout: 60_000,
    });
    expect(await page.getByText("failed", { exact: true }).count()).toBe(0);

    await page.getByRole("button", { name: "Terrain", exact: true }).click();
    await page.locator("select").first().selectOption({ index: 1 });
    await expect(page.getByRole("button", { name: "3D Terrain" })).toBeVisible({ timeout: 15_000 });

    // --- 3: disaster screening (produces the slope artifact) ---
    await page.getByRole("button", { name: "Disaster", exact: true }).click();
    await expect(page.getByText("Screening, not prediction")).toBeVisible({ timeout: 10_000 });
    await page.getByPlaceholder("e.g. 121.0").fill("125");
    await page.getByRole("button", { name: "Run screening" }).click();
    await expect(page.getByText("Terrain statistics")).toBeVisible({ timeout: 30_000 });

    // --- 4: Terrain (shared 2D workspace) ---
    await page.getByRole("button", { name: "Terrain", exact: true }).click();
    const metricElevationRow = page
      .getByRole("button", { name: "Metric Elevation", exact: true })
      .locator("xpath=..");
    await metricElevationRow.getByRole("checkbox", { name: "Visible" }).click();

    const overlayImage = page.locator(".leaflet-image-layer").last();
    await expect(overlayImage, "the real raster overlay must have actually rendered").toBeVisible({
      timeout: 10_000,
    });
    async function clickAt(fracX: number, fracY: number) {
      await overlayImage.scrollIntoViewIfNeeded();
      const box = await overlayImage.boundingBox();
      expect(box, "the real raster overlay must have a real bounding box").not.toBeNull();
      await page.mouse.click(box!.x + box!.width * fracX, box!.y + box!.height * fracY);
    }

    // --- 5: Slope at point ---
    await page.getByRole("button", { name: "Slope at point" }).click();
    await expect(page.getByText("Measurement mode: Slope at point", { exact: false })).toBeVisible({
      timeout: 10_000,
    });

    // --- 6: click a valid 2D map location (centre; the fixture's NoData
    // block is the top-left 5x5 cells) ---
    const slopeResponsePromise = page.waitForResponse(
      (r) => r.url().includes("/measurements/slope") && r.request().method() === "GET",
      { timeout: 15_000 },
    );
    await clickAt(0.5, 0.5);
    const slopeResponse = await slopeResponsePromise;
    expect(slopeResponse.status()).toBe(200);

    // --- 7: result panel content ---
    await expect(page.getByText("Measurement result")).toBeVisible({ timeout: 15_000 });
    const slopeLine = page.getByText(/^Slope: -?\d+\.\d{2}°$/);
    await expect(slopeLine).toBeVisible();
    await expect(page.getByText("Units: degrees", { exact: true })).toBeVisible();

    // --- 8: resolved row/col from the real API response ---
    const slopeData = (await slopeResponse.json()) as {
      row: number;
      col: number;
      in_bounds: boolean;
      value: number | null;
      units: string;
      value_kind: string;
    };
    expect(slopeData.in_bounds).toBe(true);
    expect(slopeData.value_kind).toBe("slope");
    expect(slopeData.units).toBe("degrees");
    expect(slopeData.value, "centre click must land on a valid slope pixel").not.toBeNull();

    // --- 9: authoritative slope raster value at that row/col ---
    const slopeUrl = new URL(slopeResponse.url());
    const downloadUrl = `${slopeUrl.origin}${slopeUrl.pathname.replace(
      /\/measurements\/slope$/,
      "/download",
    )}`;
    const authorization = (await slopeResponse.request().allHeaders())["authorization"];
    expect(authorization, "the slope request must carry the real bearer token").toBeTruthy();
    const download = await page.request.get(downloadUrl, { headers: { Authorization: authorization } });
    expect(download.status()).toBe(200);
    const tifPath = test.info().outputPath("slope.tif");
    writeFileSync(tifPath, await download.body());
    const rasterValue = readRasterValue(tifPath, slopeData.row, slopeData.col);
    expect(rasterValue, "the authoritative slope pixel must hold data").not.toBeNull();

    // --- 10: returned and displayed slope equal the raster value ---
    expect(slopeData.value).toBe(rasterValue);
    await expect(slopeLine).toHaveText(`Slope: ${rasterValue!.toFixed(2)}°`);

    // --- 11: save ---
    const saveResponsePromise = page.waitForResponse(
      (r) => /\/projects\/[0-9a-f-]+\/measurements$/.test(new URL(r.url()).pathname) &&
        r.request().method() === "POST",
      { timeout: 15_000 },
    );
    await page.getByRole("button", { name: "Save measurement" }).click();
    const saveResponse = await saveResponsePromise;
    expect(saveResponse.ok()).toBe(true);
    const saved = (await saveResponse.json()) as {
      measurement_type: string;
      result_data: { value: number | null; row: number; col: number };
    };
    expect(saved.measurement_type).toBe("point_slope");
    expect(saved.result_data.value).toBe(rasterValue);

    // --- 12: history ---
    const history = page
      .getByRole("heading", { name: "Saved measurements" })
      .locator("xpath=..");
    await expect(history).toContainText("Slope at point", { timeout: 10_000 });

    // --- 13: Point elevation, Distance, Profile still work ---
    await page.getByRole("button", { name: "Point elevation" }).click();
    await clickAt(0.5, 0.5);
    await expect(page.getByText("Measurement result")).toBeVisible({ timeout: 15_000 });
    await expect(slopeLine).toHaveCount(0);

    await page.getByRole("button", { name: "Distance" }).click();
    await expect(page.getByText("Measurement mode: Distance", { exact: false })).toBeVisible();
    await clickAt(0.25, 0.5);
    await expect(page.getByText("Click 1 more point", { exact: false })).toBeVisible({
      timeout: 10_000,
    });
    await clickAt(0.75, 0.5);
    await expect(page.getByText("Measurement result")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Point 1:", { exact: false })).toBeVisible();
    await expect(page.getByText("Point 2:", { exact: false })).toBeVisible();

    await page.getByRole("button", { name: "Profile" }).click();
    await clickAt(0.3, 0.3);
    await clickAt(0.7, 0.7);
    await expect(page.getByText("samples over", { exact: false })).toBeVisible({ timeout: 15_000 });

    await page.screenshot({ path: "e2e/screenshots/p14-slope-at-point.png", fullPage: true });

    // --- 14: no page or console errors ---
    expect(
      pageErrors,
      `uncaught page errors: ${pageErrors.map((e) => e.message).join("; ")}`,
    ).toEqual([]);
    expect(
      consoleErrors.map((m) => m.text()),
      "console errors during the P1-4 flow",
    ).toEqual([]);

    test.info().annotations.push({
      type: "slopeAtPoint",
      description: `row=${slopeData.row} col=${slopeData.col} api=${slopeData.value} raster=${rasterValue}`,
    });
  });
});
