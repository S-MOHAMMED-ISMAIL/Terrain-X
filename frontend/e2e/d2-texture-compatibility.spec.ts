import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { Page, Response } from "@playwright/test";

// D2 acceptance: the RGB texture is draped on the 3D terrain only when the
// terrain grid's pixels ARE the source image's pixels. Two runs of the same
// uncalibrated pipeline on 32 x 32 RGB sources that differ ONLY in CRS:
//   - calibrated-source.tif (EPSG:32633, projected): texture available and
//     actually applied to the mesh material;
//   - geographic-source.tif (EPSG:4326): the terrain grid is reprojected to
//     UTM, so the texture is unavailable — although every dimension matches.
// State is read from the backend context and the app's own data attributes
// (the texture toggle, and the 3D view's `data-texture-applied`, set only
// once a texture is really on the material) — never from screenshots.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(__dirname, "fixtures");

interface TerrainContext {
  available: boolean;
  width: number | null;
  height: number | null;
  crs: string | null;
  texture_compatible: boolean | null;
  texture_unavailable_code: string | null;
  texture_unavailable_reason: string | null;
}

function uniqueEmail(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.floor(Math.random() * 1e6)}@example.com`;
}

async function registerAndLogin(page: Page, prefix: string) {
  await page.goto("/register");
  await page.locator('input[type="email"]').fill(uniqueEmail(prefix));
  await page.locator('input[type="password"]').fill("D2TexturePass123!");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/dashboard/, { timeout: 15_000 });
  await page.goto("/projects");
  const name = `D2 Texture ${Date.now()}`;
  await page.getByPlaceholder("e.g. Coastal Flood Study").fill(name);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("link", { name }).click();
  await page.waitForURL(/\/projects\/[0-9a-f-]+/, { timeout: 15_000 });
}

/** Uploads `fixture`, runs the uncalibrated analysis, opens the 3D view and
 * returns the backend's own terrain context. */
async function openTerrain(page: Page, fixture: string): Promise<TerrainContext> {
  await page.locator('input[type="file"]').first().setInputFiles(path.join(FIXTURES, fixture));
  await expect(page.getByText("valid", { exact: false }).first()).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.locator("select").first().selectOption({ index: 1 });
  await page.getByRole("button", { name: "Start Analysis" }).click();
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({
    timeout: 90_000,
  });
  const contextPromise = page.waitForResponse(
    (r: Response) => r.url().includes("/visualization/context") && r.status() === 200,
  );
  await page.getByRole("button", { name: "Terrain", exact: true }).click();
  await page.locator("select").first().selectOption({ index: 1 });
  const context = (await (await contextPromise).json()) as {
    terrain: TerrainContext;
    layers: { layer_type: string; width: number | null; height: number | null }[];
  };
  await expect(page.getByRole("button", { name: "3D Terrain" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "3D Terrain" }).click();
  await expect(page.getByTestId("terrain-3d-canvas")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Loading terrain height grid")).toHaveCount(0, { timeout: 20_000 });
  const rgb = context.layers.find((l) => l.layer_type === "rgb")!;
  // Both fixtures: the RGB and the terrain artifact have the same 32 x 32
  // dimensions — the pre-D2 browser rule would enable the texture for both.
  expect([rgb.width, rgb.height]).toEqual([32, 32]);
  expect([context.terrain.width, context.terrain.height]).toEqual([32, 32]);
  return context.terrain;
}

test.describe("D2: RGB texture only on pixel-corresponding terrain", () => {
  test("projected source: texture available and applied to the mesh", async ({ page }) => {
    await registerAndLogin(page, "d2proj");
    const terrain = await openTerrain(page, "calibrated-source.tif");
    expect(terrain.crs).toBe("EPSG:32633");
    expect(terrain.texture_compatible).toBe(true);
    expect(terrain.texture_unavailable_code).toBeNull();

    const toggle = page.getByTestId("texture-toggle");
    await expect(toggle).toBeEnabled();
    await expect(toggle).toHaveAttribute("data-texture-compatible", "true");
    const view = page.getByTestId("terrain-3d-canvas");
    // The toggle is on by default: the texture is applied once loaded.
    await expect(toggle).toBeChecked();
    await expect(view).toHaveAttribute("data-texture-applied", "true", { timeout: 10_000 });
    await toggle.uncheck();
    await expect(view).toHaveAttribute("data-texture-applied", "false");
    const preview = page.waitForResponse(
      (r) => /\/datasets\/[0-9a-f-]+\/visualization\/preview$/.test(new URL(r.url()).pathname),
    );
    await toggle.check();
    expect((await preview).status()).toBe(200);
    await expect(view).toHaveAttribute("data-texture-applied", "true", { timeout: 10_000 });
  });

  test("geographic source: reprojected terrain never receives the texture", async ({ page }) => {
    await registerAndLogin(page, "d2geo");
    let previewRequests = 0;
    page.on("request", (r) => {
      if (/\/datasets\/[0-9a-f-]+\/visualization\/preview$/.test(new URL(r.url()).pathname)) {
        previewRequests++;
      }
    });
    const terrain = await openTerrain(page, "geographic-source.tif");
    expect(terrain.crs).toBe("EPSG:4326");
    expect(terrain.texture_compatible).toBe(false);
    expect(terrain.texture_unavailable_code).toBe("reprojected_terrain_grid");
    expect(terrain.texture_unavailable_reason).toMatch(/reprojected/);

    const toggle = page.getByTestId("texture-toggle");
    await expect(toggle).toBeDisabled();
    // The toggle defaults to on, yet nothing is draped.
    await expect(toggle).toHaveAttribute("data-texture-compatible", "false");
    await expect(toggle).toHaveAttribute("data-texture-code", "reprojected_terrain_grid");
    await expect(page.getByText("(not spatially compatible)")).toBeVisible();
    // Even a forced click cannot attach it: no preview is fetched for the
    // 3D view and the material keeps no texture.
    await toggle.click({ force: true });
    await page.waitForTimeout(1_000);
    await expect(page.getByTestId("terrain-3d-canvas")).toHaveAttribute(
      "data-texture-applied",
      "false",
    );
    expect(previewRequests).toBe(0);
  });
});
