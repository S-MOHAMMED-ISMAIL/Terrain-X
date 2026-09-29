import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// P1-3: raster bare-earth approximation (DTM) + nDSM, end to end against the
// real running application (real backend, real worker, real Depth Anything,
// real P1-2 calibration gate) — no mocked API.
//
// The calibrated fixtures are DELIBERATELY depth-consistent (see
// e2e/fixtures/README.md): they validate pipeline behavior and are NOT
// accuracy evidence. DTM/nDSM are raster-derived ESTIMATES.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(__dirname, "fixtures");
const API = "http://localhost:8000/api/v1";

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

async function apiGet(page: Page, url: string) {
  const token = await page.evaluate(() => localStorage.getItem("terrainx_access_token"));
  const resp = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
  expect(resp.status(), url).toBe(200);
  return resp.json();
}

function layerRow(page: Page, displayName: string) {
  return page.getByRole("button", { name: displayName, exact: true }).locator("xpath=..");
}

test.describe("P1-3: DTM / nDSM estimates", () => {
  test("a gate-passed calibrated job produces DTM/nDSM layers that toggle and inspect honestly, with the DSM still the terrain source", async ({
    page,
  }) => {
    const pageErrors: Error[] = [];
    page.on("pageerror", (err) => pageErrors.push(err));

    await registerAndLogin(page, uniqueEmail("p13dtm"), "P13DtmNdsmPass123!");
    await page.getByRole("link", { name: "Projects" }).first().click();
    const projectName = `P1-3 DTM nDSM ${Date.now()}`;
    await page.getByPlaceholder("e.g. Coastal Flood Study").fill(projectName);
    await page.getByRole("button", { name: "Create project" }).click();
    await page.getByRole("link", { name: projectName }).click();
    await page.waitForURL(/\/projects\/[0-9a-f-]+/, { timeout: 15_000 });
    const projectId = page.url().match(/projects\/([0-9a-f-]+)/)?.[1];

    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(FIXTURES, "calibrated-source.tif"),
    );
    await expect(page.getByText("valid", { exact: false }).first()).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(FIXTURES, "calibrated-dem.tif"),
    );
    await expect(page.getByText("valid", { exact: false }).nth(1)).toBeVisible({ timeout: 20_000 });

    // --- Real calibrated analysis ---
    await page.getByRole("button", { name: "Analysis" }).click();
    await page.locator("select").first().selectOption({ index: 1 });
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator("select").nth(1).selectOption({ index: 1 });
    await page.getByRole("button", { name: "Start Analysis" }).click();
    await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({
      timeout: 60_000,
    });
    expect(await page.getByText("failed", { exact: true }).count()).toBe(0);
    await expect(page.getByRole("button", { name: "Download DTM (estimate)" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Download nDSM (estimate)" })).toBeVisible();

    // --- The calibrated job really produced DTM/nDSM, only after a passed gate ---
    const jobs = await apiGet(page, `${API}/projects/${projectId}/analysis`);
    const job = jobs.find(
      (j: { parameters: { dem_reference_dataset_id?: string } }) =>
        j.parameters.dem_reference_dataset_id,
    );
    expect(job.calibration_status).toBe("calibrated");
    expect(job.calibration_metadata.quality_gate.passed).toBe(true);
    expect(job.ground_filter_status).toBe("completed");
    const artifacts = await apiGet(page, `${API}/projects/${projectId}/analysis/${job.id}/artifacts`);
    const types = artifacts.map((a: { artifact_type: string }) => a.artifact_type).sort();
    // P1-5 adds the sample-point calibration residuals to a gate-passed job.
    expect(types).toEqual([
      "calibration_residuals",
      "dsm",
      "dtm",
      "metric_elevation",
      "ndsm",
      "relative_depth",
    ]);

    // --- Terrain workspace: the layers exist and are available ---
    await page.getByRole("button", { name: "Terrain", exact: true }).click();
    await page.locator("select").first().selectOption({ index: 1 });
    await expect(page.getByRole("button", { name: "3D Terrain" })).toBeVisible({ timeout: 15_000 });

    const dtmVisible = layerRow(page, "DTM (estimated bare earth)").getByRole("checkbox", {
      name: "Visible",
    });
    const ndsmVisible = layerRow(page, "nDSM (estimated height above ground)").getByRole(
      "checkbox",
      { name: "Visible" },
    );
    await expect(dtmVisible).toBeEnabled();
    await expect(ndsmVisible).toBeEnabled();

    const overlay = page.locator(".leaflet-image-layer").last();
    async function clickCenter() {
      await overlay.scrollIntoViewIfNeeded();
      const box = await overlay.boundingBox();
      expect(box, "the real raster overlay must have a real bounding box").not.toBeNull();
      await page.mouse.click(box!.x + box!.width * 0.5, box!.y + box!.height * 0.5);
    }

    // --- Toggle nDSM on: inspection reports an estimated height above ground ---
    await ndsmVisible.click();
    await expect(ndsmVisible).toBeChecked();
    await expect(overlay).toBeVisible({ timeout: 10_000 });
    await clickCenter();
    await expect(page.getByText("Estimated height above ground:", { exact: false })).toBeVisible({
      timeout: 10_000,
    });

    // --- Toggle nDSM off and DTM on: inspection reports estimated bare earth ---
    await ndsmVisible.click();
    await expect(ndsmVisible).not.toBeChecked();
    await dtmVisible.click();
    await expect(dtmVisible).toBeChecked();
    await clickCenter();
    await expect(page.getByText("Estimated bare-earth elevation:", { exact: false })).toBeVisible({
      timeout: 10_000,
    });
    await dtmVisible.click();
    await expect(dtmVisible).not.toBeChecked();

    // --- Terrain source unchanged: still the calibrated DSM ---
    const sourceId = job.dataset_id;
    const context = await apiGet(
      page,
      `${API}/projects/${projectId}/datasets/${sourceId}/visualization/context`,
    );
    expect(context.terrain.source_artifact_type).toBe("dsm");
    expect(context.terrain.height_kind).toBe("elevation");
    const dsmArtifact = artifacts.find((a: { artifact_type: string }) => a.artifact_type === "dsm");
    expect(context.terrain.artifact_id).toBe(dsmArtifact.id);

    await page.getByRole("button", { name: "3D Terrain" }).click();
    await expect(page.locator("canvas").first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Loading terrain height grid")).toHaveCount(0, { timeout: 20_000 });
    await expect(page.getByText("NOT elevation, NOT a DSM", { exact: false })).toHaveCount(0);

    await page.screenshot({ path: "e2e/screenshots/p13-dtm-ndsm.png", fullPage: true });
    expect(
      pageErrors,
      `uncaught page errors: ${pageErrors.map((e) => e.message).join("; ")}`,
    ).toEqual([]);
  });
});
