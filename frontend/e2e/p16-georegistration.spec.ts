import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { ConsoleMessage, Page, Response } from "@playwright/test";

// P1-6 acceptance: 2D georegistration + backend-authoritative pixel
// resolution, against the ACTUAL running application on the OFF-MERIDIAN
// fixture (UTM 33N, ~60 N, 2.9 deg east of the central meridian), where the
// pre-P1-6 map drew the image up to ~220 m off and clicks read the wrong
// source pixel. Every check is numerical against independently read raster
// data (rasterio, as in P1-4) — never a screenshot.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(__dirname, "fixtures");
const R = 6378137; // EPSG:3857 sphere radius

interface ResidualProps {
  sample_index: number;
  row: number;
  col: number;
  inlier_in_production_fit: boolean;
}
interface ResidualFeature {
  geometry: { coordinates: [number, number] };
  properties: ResidualProps;
}
interface Bounds {
  min_x: number;
  min_y: number;
  max_x: number;
  max_y: number;
}
interface Layer {
  layer_type: string;
  artifact_id: string | null;
  analysis_job_id: string | null;
  map_overlay_bounds: Bounds | null;
}

function uniqueEmail(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.floor(Math.random() * 1e6)}@example.com`;
}

const mercX = (lon: number) => (R * lon * Math.PI) / 180;
const mercY = (lat: number) => R * Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
const lonOf = (x: number) => (x / R) * (180 / Math.PI);
const latOf = (y: number) => (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * (180 / Math.PI);

/** Independent rasterio read of a downloaded GeoTIFF: for each WGS84 point,
 * the (row, col) the raster's own transform puts it in and the value there,
 * plus the colour the map overlay ramp gives that value. */
function readPoints(tifPath: string, points: [number, number][]) {
  const script = [
    "import json, sys, numpy as np, rasterio",
    "from rasterio.warp import transform",
    "sys.path.insert(0, sys.argv[3])",
    "from geospatial.raster_preview import _colorize_single_band",
    "pts = json.loads(sys.argv[2])",
    "with rasterio.open(sys.argv[1]) as src:",
    "    band = src.read(1).astype('float64')",
    "    valid = band[np.isfinite(band) & (band != src.nodata)] if src.nodata is not None else band[np.isfinite(band)]",
    "    rng = (float(valid.min()), float(valid.max()))",
    "    xs, ys = transform('EPSG:4326', src.crs, [p[0] for p in pts], [p[1] for p in pts])",
    "    out = []",
    "    for x, y in zip(xs, ys):",
    "        col, row = ~src.transform * (x, y)",
    "        row, col = int(np.floor(row)), int(np.floor(col))",
    "        v = float(band[row, col])",
    "        rgba = _colorize_single_band(np.array([[v]]), src.nodata, rng)[0, 0].tolist()",
    "        out.append({'row': row, 'col': col, 'value': v, 'rgba': rgba})",
    "print(json.dumps(out))",
  ].join("\n");
  const repoRoot = path.resolve(__dirname, "..", "..");
  const stdout = execFileSync(
    "python",
    ["-c", script, tifPath, JSON.stringify(points), repoRoot],
    { encoding: "utf-8" },
  );
  return JSON.parse(stdout.trim()) as { row: number; col: number; value: number; rgba: number[] }[];
}

async function registerAndLogin(page: Page, email: string, password: string) {
  await page.goto("/register");
  await page.locator('input[type="email"]').fill(email);
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/dashboard/, { timeout: 15_000 });
}

test.describe("P1-6: 2D georegistration (off-meridian)", () => {
  test("clicks resolve the true source pixel, tools agree, and the overlay draws the right pixel", async ({
    page,
  }) => {
    const consoleErrors: ConsoleMessage[] = [];
    const pageErrors: Error[] = [];
    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m);
    });
    page.on("pageerror", (e) => pageErrors.push(e));

    await registerAndLogin(page, uniqueEmail("p16georeg"), "P16GeoregPass123!");
    await page.goto("/projects");
    const projectName = `P1-6 Georeg ${Date.now()}`;
    await page.getByPlaceholder("e.g. Coastal Flood Study").fill(projectName);
    await page.getByRole("button", { name: "Create project" }).click();
    await page.getByRole("link", { name: projectName }).click();
    await page.waitForURL(/\/projects\/[0-9a-f-]+/, { timeout: 15_000 });

    // --- off-meridian calibrated fixture + calibrated analysis ---
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(FIXTURES, "offmeridian-source.tif"),
    );
    await expect(page.getByText("valid", { exact: false }).first()).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(FIXTURES, "offmeridian-dem.tif"),
    );
    await expect(page.getByText("valid", { exact: false }).nth(1)).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: "Analysis" }).click();
    await page.locator("select").first().selectOption({ index: 1 });
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator("select").nth(1).selectOption({ index: 1 });
    await page.getByRole("button", { name: "Start Analysis" }).click();
    await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({
      timeout: 90_000,
    });
    expect(await page.getByText("failed", { exact: true }).count()).toBe(0);

    // --- Terrain: capture the stored P1-5 residuals (true sample positions) ---
    await page.getByRole("button", { name: "Terrain", exact: true }).click();
    const residualsPromise = page.waitForResponse(
      (r: Response) => r.url().includes("/calibration-residuals") && r.request().method() === "GET",
      { timeout: 20_000 },
    );
    await page.locator("select").first().selectOption({ index: 1 });
    const residualsResponse = await residualsPromise;
    expect(residualsResponse.status()).toBe(200);
    const features = (await residualsResponse.json()).feature_collection
      .features as ResidualFeature[];
    const authorization = (await residualsResponse.request().allHeaders())["authorization"];
    const apiRoot = residualsResponse.url().split("/projects/")[0];
    const projectId = residualsResponse.url().split("/projects/")[1].split("/")[0];

    // --- disaster screening (slope-at-point needs the slope raster) ---
    await page.getByRole("button", { name: "Disaster", exact: true }).click();
    await expect(page.getByText("Screening, not prediction")).toBeVisible({ timeout: 10_000 });
    await page.getByPlaceholder("e.g. 121.0").fill("120");
    await page.getByRole("button", { name: "Run screening" }).click();
    await expect(page.getByText("Terrain statistics")).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: "Terrain", exact: true }).click();

    // --- the real context: dataset envelope and each layer's own overlay bounds ---
    const sourceDatasetId = (
      await (
        await page.request.get(`${apiRoot}/projects/${projectId}/datasets`, {
          headers: { Authorization: authorization },
        })
      ).json()
    ).find((d: { role: string }) => d.role === "source_image").id as string;
    const context = await (
      await page.request.get(
        `${apiRoot}/projects/${projectId}/datasets/${sourceDatasetId}/visualization/context`,
        { headers: { Authorization: authorization } },
      )
    ).json();
    const layers = new Map<string, Layer>(
      (context.layers as Layer[]).map((l) => [l.layer_type, l]),
    );
    const dsm = layers.get("dsm")!;
    const slope = layers.get("slope")!;
    expect(dsm.map_overlay_bounds).not.toBeNull();
    expect(slope.map_overlay_bounds).not.toBeNull();
    const envelope = context.dataset.bounds_wgs84 as Bounds;

    // --- pick a residual sample the OLD envelope mapping got wrong ---
    const oldPixel = (lon: number, lat: number) => ({
      col: Math.floor(((lon - envelope.min_x) / (envelope.max_x - envelope.min_x)) * 128),
      row: Math.floor(((envelope.max_y - lat) / (envelope.max_y - envelope.min_y)) * 128),
    });
    const candidates = features
      .map((f) => {
        const [lon, lat] = f.geometry.coordinates;
        const old = oldPixel(lon, lat);
        const error = Math.hypot(old.col - f.properties.col, old.row - f.properties.row);
        return { f, error };
      })
      .filter(
        ({ f }) =>
          f.properties.inlier_in_production_fit &&
          f.properties.row > 10 &&
          f.properties.row < 118 &&
          f.properties.col > 10 &&
          f.properties.col < 118,
      )
      .sort((a, b) => b.error - a.error);
    const { f: target, error: oldError } = candidates[0];
    expect(oldError, "the fixture must expose the pre-P1-6 defect").toBeGreaterThanOrEqual(3);
    const [targetLon, targetLat] = target.geometry.coordinates;

    // --- DSM visible, residual markers on; locate the target marker on screen ---
    await page
      .getByRole("button", { name: "DSM", exact: true })
      .locator("xpath=..")
      .getByRole("checkbox", { name: "Visible" })
      .click();
    await page.getByRole("checkbox", { name: "Show calibration residuals" }).check();
    const marker = page.locator(`path.residual-sample-${target.properties.sample_index}`);
    await expect(marker).toHaveCount(1, { timeout: 10_000 });
    const mapContainer = page.locator(".leaflet-container");

    async function clickTarget() {
      await mapContainer.scrollIntoViewIfNeeded();
      const box = (await marker.boundingBox())!;
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    }

    // --- point elevation at the marker: backend row/col == residual row/col ---
    await page.getByRole("button", { name: "Point elevation" }).click();
    const pixelPromise = page.waitForResponse(
      (r) => r.url().includes("/measurements/pixel") && r.url().includes(dsm.artifact_id!),
      { timeout: 15_000 },
    );
    const pointPromise = page.waitForResponse(
      (r) => r.url().includes("/measurements/point?"),
      { timeout: 15_000 },
    );
    await clickTarget();
    const resolved = await (await pixelPromise).json();
    expect(resolved.in_bounds).toBe(true);
    expect([resolved.row, resolved.col]).toEqual([target.properties.row, target.properties.col]);
    const point = await (await pointPromise).json();
    expect([point.row, point.col]).toEqual([target.properties.row, target.properties.col]);
    await expect(page.getByText("Measurement result")).toBeVisible({ timeout: 15_000 });

    // --- independently read DSM value at that pixel ---
    const download = await page.request.get(
      `${apiRoot}/projects/${projectId}/analysis/${dsm.analysis_job_id}/artifacts/${dsm.artifact_id}/download`,
      { headers: { Authorization: authorization } },
    );
    expect(download.status()).toBe(200);
    const dsmPath = test.info().outputPath("dsm.tif");
    writeFileSync(dsmPath, await download.body());
    const [atTarget] = readPoints(dsmPath, [[targetLon, targetLat]]);
    expect([atTarget.row, atTarget.col]).toEqual([target.properties.row, target.properties.col]);
    expect(point.value).toBe(atTarget.value);
    await expect(page.getByText(`Elevation: ${atTarget.value.toFixed(3)}`)).toBeVisible();

    // --- slope-at-point at the same screen location resolves the same pixel ---
    await page.getByRole("button", { name: "Slope at point" }).click();
    const slopePromise = page.waitForResponse(
      (r) => r.url().includes("/measurements/slope") && r.request().method() === "GET",
      { timeout: 15_000 },
    );
    await clickTarget();
    const slopeResult = await (await slopePromise).json();
    expect(slopeResult.reprojected_for_analysis).toBe(false); // same (source) grid as the DSM
    expect([slopeResult.row, slopeResult.col]).toEqual([resolved.row, resolved.col]);

    // --- plain inspection at the same spot reads the same pixel ---
    // Markers take clicks in inspection mode, so hide them first and click
    // the same map-relative position the marker had (the map does not move).
    await page.getByRole("button", { name: "Inspect (off)", exact: true }).click();
    await mapContainer.scrollIntoViewIfNeeded();
    const markerBox = (await marker.boundingBox())!;
    const beforeBox = (await mapContainer.boundingBox())!;
    const offset = {
      x: markerBox.x + markerBox.width / 2 - beforeBox.x,
      y: markerBox.y + markerBox.height / 2 - beforeBox.y,
    };
    await page.getByRole("checkbox", { name: "Show calibration residuals" }).uncheck();
    await expect(page.locator("path.residual-marker")).toHaveCount(0);
    const inspectPixelPromise = page.waitForResponse(
      (r) => r.url().includes("/measurements/pixel"),
      { timeout: 15_000 },
    );
    const valuePromise = page.waitForResponse((r) => r.url().includes("/visualization/value"), {
      timeout: 15_000,
    });
    await mapContainer.scrollIntoViewIfNeeded();
    const containerBox = (await mapContainer.boundingBox())!;
    await page.mouse.click(containerBox.x + offset.x, containerBox.y + offset.y);
    const inspectPixel = await (await inspectPixelPromise).json();
    expect([inspectPixel.row, inspectPixel.col]).toEqual([resolved.row, resolved.col]);
    const inspectValue = await (await valuePromise).json();
    expect(inspectValue.value).toBe(atTarget.value);

    // --- the DSM map-preview overlay draws the right source pixel ---
    // The overlay pixel containing the target: its centre lies in some source
    // pixel (by the DSM's own transform); the overlay must show exactly that
    // pixel's value, and it must be the target pixel or an immediate
    // neighbour (the old envelope stretch was >= 3 px off here).
    const b = dsm.map_overlay_bounds!;
    const overlay = await page.evaluate(
      async ({ url, auth }) => {
        const blob = await (await fetch(url, { headers: { Authorization: auth } })).blob();
        const bitmap = await createImageBitmap(blob);
        const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
        const ctx = canvas.getContext("2d")!;
        ctx.drawImage(bitmap, 0, 0);
        return {
          width: bitmap.width,
          height: bitmap.height,
          data: Array.from(ctx.getImageData(0, 0, bitmap.width, bitmap.height).data),
        };
      },
      {
        url: `${apiRoot}/projects/${projectId}/analysis/${dsm.analysis_job_id}/artifacts/${dsm.artifact_id}/visualization/map-preview`,
        auth: authorization,
      },
    );
    const x0 = mercX(b.min_x);
    const x1 = mercX(b.max_x);
    const y0 = mercY(b.min_y);
    const y1 = mercY(b.max_y);
    const oc = Math.floor(((mercX(targetLon) - x0) / (x1 - x0)) * overlay.width);
    const or = Math.floor(((y1 - mercY(targetLat)) / (y1 - y0)) * overlay.height);
    const centreLon = lonOf(x0 + ((oc + 0.5) / overlay.width) * (x1 - x0));
    const centreLat = latOf(y1 - ((or + 0.5) / overlay.height) * (y1 - y0));
    const [atCentre] = readPoints(dsmPath, [[centreLon, centreLat]]);
    const i = (or * overlay.width + oc) * 4;
    expect(overlay.data.slice(i, i + 4)).toEqual(atCentre.rgba);
    expect(overlay.data[i + 3]).toBe(255);
    expect(
      Math.max(
        Math.abs(atCentre.row - target.properties.row),
        Math.abs(atCentre.col - target.properties.col),
      ),
    ).toBeLessThanOrEqual(1);

    expect(pageErrors.map((e) => e.message)).toEqual([]);
    expect(consoleErrors.map((m) => m.text())).toEqual([]);

    const old = oldPixel(targetLon, targetLat);
    test.info().annotations.push({
      type: "georegistration",
      description:
        `target sample ${target.properties.sample_index} at (row ${target.properties.row}, col ${target.properties.col}); ` +
        `old envelope mapping -> (row ${old.row}, col ${old.col}), ${oldError.toFixed(2)} px off; ` +
        `backend -> (row ${resolved.row}, col ${resolved.col}); point elevation ${point.value} == raster ${atTarget.value}; ` +
        `slope pixel (row ${slopeResult.row}, col ${slopeResult.col}); ` +
        `overlay px (${or}, ${oc}) -> source (row ${atCentre.row}, col ${atCentre.col}) rgba ${atCentre.rgba.join(",")}`,
    });
  });
});
