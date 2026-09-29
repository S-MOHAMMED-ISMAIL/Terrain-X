import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { ConsoleMessage, Page, Response } from "@playwright/test";

// P1-7 acceptance: terrain-aware first-person flythrough against the ACTUAL
// running application. Camera state is validated numerically through the
// flythrough HUD (whose values are also exposed at full precision in
// data-value attributes) — never by screenshots. The expected display-grid
// ground value is computed independently here from the app's own
// /terrain/grid bytes + /terrain/metadata, and HUD map coordinates are
// resolved through the existing backend /measurements/pixel endpoint.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(__dirname, "fixtures");

interface TerrainMeta {
  width: number;
  height: number;
  source_width: number;
  source_height: number;
  is_georeferenced: boolean;
  local_crs: string | null;
  origin_x: number | null;
  origin_y: number | null;
  cell_size_x: number | null;
  cell_size_y: number | null;
  height_kind: string;
}

interface HudSample {
  mapX: number | null;
  mapY: number | null;
  pixelCol: number | null;
  pixelRow: number | null;
  ground: number | null;
  clearance: number | null;
  minClearance: number;
  heading: number;
  speed: number;
  follow: string;
  groundText: string;
  clearanceText: string;
}

function uniqueEmail(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.floor(Math.random() * 1e6)}@example.com`;
}

const num = (v: string | null) => (v === null || v === "" ? null : Number(v));

async function readHud(page: Page): Promise<HudSample> {
  const pos = page.getByTestId("flight-hud-position");
  const ground = page.getByTestId("flight-hud-ground");
  const clearance = page.getByTestId("flight-hud-clearance");
  return {
    mapX: num(await pos.getAttribute("data-map-x")),
    mapY: num(await pos.getAttribute("data-map-y")),
    pixelCol: num(await pos.getAttribute("data-pixel-col")),
    pixelRow: num(await pos.getAttribute("data-pixel-row")),
    ground: num(await ground.getAttribute("data-value")),
    clearance: num(await clearance.getAttribute("data-value")),
    minClearance: Number(await clearance.getAttribute("data-min")),
    heading: Number(await page.getByTestId("flight-hud-heading").getAttribute("data-value")),
    speed: Number(await page.getByTestId("flight-hud-speed").getAttribute("data-value")),
    follow: (await page.getByTestId("flight-hud-follow").getAttribute("data-value")) ?? "",
    groundText: await ground.innerText(),
    clearanceText: await clearance.innerText(),
  };
}

/** Independent display-grid ground value: the mesh's triangle rule (quad
 * split along (r, c+1)-(r+1, c), a quad only if all four corners finite). */
function expectedGround(meta: TerrainMeta, grid: Float32Array, col: number, row: number) {
  const { width: w, height: h } = meta;
  if (col < 0 || row < 0 || col > w - 1 || row > h - 1) return null;
  const c0 = Math.min(Math.floor(col), w - 2);
  const r0 = Math.min(Math.floor(row), h - 2);
  const a = grid[r0 * w + c0];
  const b = grid[r0 * w + c0 + 1];
  const c = grid[(r0 + 1) * w + c0];
  const d = grid[(r0 + 1) * w + c0 + 1];
  if (![a, b, c, d].every(Number.isFinite)) return null;
  const u = col - c0;
  const v = row - r0;
  return u + v <= 1 ? a + u * (b - a) + v * (c - a) : d + (1 - u) * (c - d) + (1 - v) * (b - d);
}

async function registerAndLogin(page: Page, prefix: string) {
  await page.goto("/register");
  await page.locator('input[type="email"]').fill(uniqueEmail(prefix));
  await page.locator('input[type="password"]').fill("P17FlightPass123!");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/dashboard/, { timeout: 15_000 });
  await page.goto("/projects");
  const name = `P1-7 Flight ${Date.now()}`;
  await page.getByPlaceholder("e.g. Coastal Flood Study").fill(name);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("link", { name }).click();
  await page.waitForURL(/\/projects\/[0-9a-f-]+/, { timeout: 15_000 });
}

async function runAnalysis(page: Page, withDem: boolean) {
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.locator("select").first().selectOption({ index: 1 });
  if (withDem) {
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator("select").nth(1).selectOption({ index: 1 });
  }
  await page.getByRole("button", { name: "Start Analysis" }).click();
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({
    timeout: 90_000,
  });
  expect(await page.getByText("failed", { exact: true }).count()).toBe(0);
}

/** Opens the 3D view and returns the app's own terrain metadata + grid. */
async function open3D(page: Page) {
  await page.getByRole("button", { name: "Terrain", exact: true }).click();
  await page.locator("select").first().selectOption({ index: 1 });
  await expect(page.getByRole("button", { name: "3D Terrain" })).toBeVisible({ timeout: 15_000 });
  const metaPromise = page.waitForResponse((r: Response) =>
    r.url().includes("/visualization/terrain/metadata"),
  );
  await page.getByRole("button", { name: "3D Terrain" }).click();
  const metaResponse = await metaPromise;
  const meta = (await metaResponse.json()) as TerrainMeta;
  // The binary grid is fetched by a separate, independent request (the
  // browser's own capture of this cross-origin binary response is not
  // reliable — see phase10-terrain.spec.ts).
  const authorization = (await metaResponse.request().allHeaders())["authorization"];
  const gridResponse = await page.request.get(
    metaResponse.url().replace("/terrain/metadata", "/terrain/grid"),
    { headers: { Authorization: authorization } },
  );
  expect(gridResponse.status()).toBe(200);
  const gridBytes = await gridResponse.body();
  const grid = new Float32Array(
    gridBytes.buffer.slice(gridBytes.byteOffset, gridBytes.byteOffset + gridBytes.byteLength),
  );
  expect(grid.length).toBe(meta.width * meta.height);
  const canvas = page.locator("canvas:not([data-decorative-canvas])").first();
  await expect(canvas).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Loading terrain height grid")).toHaveCount(0, { timeout: 20_000 });
  return { meta, grid, canvas, metaUrl: metaResponse.url(), metaResponse };
}

async function enterFlythrough(page: Page, canvas: ReturnType<Page["locator"]>) {
  await page.getByRole("button", { name: "Flythrough mode" }).click();
  await expect(page.getByText("Click to enter flythrough mode")).toBeVisible();
  const box = (await canvas.boundingBox())!;
  await canvas.click({ position: { x: box.width / 2, y: box.height - 20 }, force: true });
  await expect
    .poll(() => page.evaluate(() => document.pointerLockElement !== null), { timeout: 5_000 })
    .toBe(true);
  await expect(page.getByTestId("flight-hud")).toBeVisible({ timeout: 5_000 });
}

/** Holds `keys` for `ms`, sampling the HUD every ~100 ms. */
async function holdAndSample(page: Page, keys: string[], ms: number): Promise<HudSample[]> {
  for (const k of keys) await page.keyboard.down(k);
  const samples: HudSample[] = [];
  const end = Date.now() + ms;
  while (Date.now() < end) {
    await page.waitForTimeout(100);
    samples.push(await readHud(page));
  }
  for (const k of keys) await page.keyboard.up(k);
  await page.waitForTimeout(250);
  samples.push(await readHud(page));
  return samples;
}

function expectClearanceNeverBelowMin(samples: HudSample[]) {
  for (const s of samples) {
    if (s.clearance !== null) {
      expect(s.clearance, JSON.stringify(s)).toBeGreaterThanOrEqual(s.minClearance - 1e-6);
    }
  }
}

function watchErrors(page: Page) {
  const console: ConsoleMessage[] = [];
  const errors: Error[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") console.push(m);
  });
  page.on("pageerror", (e) => errors.push(e));
  return () => {
    expect(errors.map((e) => e.message)).toEqual([]);
    expect(console.map((m) => m.text())).toEqual([]);
  };
}

test.describe("P1-7: terrain-aware first-person flythrough", () => {
  test("calibrated DSM: entry pose, vertical/horizontal flight, clearance, follow, speed, HUD values, exit", async ({
    page,
  }) => {
    const assertNoErrors = watchErrors(page);
    await registerAndLogin(page, "p17dsm");
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

    const { meta, grid, canvas, metaResponse } = await open3D(page);
    expect(meta.height_kind).toBe("elevation");
    const cellX = meta.cell_size_x!;
    const cellY = meta.cell_size_y!;

    // --- 1: deterministic entry pose (south-edge centre, looking north) ---
    await enterFlythrough(page, canvas);
    const entry = await readHud(page);
    // D1: grid vertex (row, col) is the centre of that cell, origin + (i + 0.5) * cell.
    expect(entry.mapX).toBeCloseTo(meta.origin_x! + ((meta.width - 1) / 2 + 0.5) * cellX, 6);
    expect(entry.mapY).toBeCloseTo(meta.origin_y! + (meta.height - 1 + 0.5) * cellY, 6);
    expect(entry.heading).toBeCloseTo(0, 6);
    expect(entry.follow).toBe("off");
    expect(entry.groundText).toMatch(/^Ground elevation \(display grid, exaggeration removed\)/);
    expect(entry.clearanceText).toMatch(/reference units/);
    expect(entry.clearance!).toBeGreaterThan(entry.minClearance);

    // --- 3/5: Shift descends and stops at ground + minimum clearance ---
    const descent = await holdAndSample(page, ["Shift"], 3_000);
    expectClearanceNeverBelowMin(descent);
    const low = descent[descent.length - 1];
    expect(low.clearance!).toBeCloseTo(low.minClearance, 3);
    // Space climbs again.
    const climb = await holdAndSample(page, ["Space"], 400);
    expect(climb[climb.length - 1].clearance!).toBeGreaterThan(low.clearance! + 0.1);

    // --- 4: + increases speed by 1.5x, - restores it ---
    const before = (await readHud(page)).speed;
    await page.keyboard.press("Equal");
    await expect.poll(async () => (await readHud(page)).speed).toBeCloseTo(before * 1.5, 6);
    await page.keyboard.press("Minus");
    await expect.poll(async () => (await readHud(page)).speed).toBeCloseTo(before, 6);

    // --- 2/5/6: fly forward (north) close to the ground; clearance holds ---
    const forward = await holdAndSample(page, ["KeyW", "Shift"], 1_500);
    expectClearanceNeverBelowMin(forward);
    expect(forward[forward.length - 1].mapY!).toBeGreaterThan(low.mapY!); // north = +y
    expect(forward[forward.length - 1].mapX!).toBeCloseTo(low.mapX!, 6);

    // --- 9: terrain-follow holds the clearance it was switched on at ---
    await holdAndSample(page, ["Space"], 300);
    await page.keyboard.press("KeyF");
    await expect.poll(async () => (await readHud(page)).follow).toBe("on");
    const followStart = (await readHud(page)).clearance!;
    const followed = await holdAndSample(page, ["KeyD"], 800);
    for (const s of followed) {
      if (s.clearance !== null) expect(s.clearance).toBeCloseTo(followStart, 3);
    }
    await page.keyboard.press("KeyF");
    await expect.poll(async () => (await readHud(page)).follow).toBe("off");

    // --- 10/11: a sampled HUD position resolves through the backend and its
    // ground value equals the independently computed display-grid value ---
    const sample = await readHud(page);
    expect(sample.mapX).not.toBeNull();
    // Fractional grid index: integer = the centre of that cell (D1).
    const col = (sample.mapX! - meta.origin_x!) / cellX - 0.5;
    const row = (sample.mapY! - meta.origin_y!) / cellY - 0.5;
    const expected = expectedGround(meta, grid, col, row);
    expect(expected).not.toBeNull();
    expect(sample.ground!).toBeCloseTo(expected!, 6);
    const [apiRoot, rest] = metaResponse.url().split("/projects/");
    const [projectId, , jobId, , artifactId] = rest.split("/");
    const authorization = (await metaResponse.request().allHeaders())["authorization"];
    const pixel = await (
      await page.request.get(
        `${apiRoot}/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/measurements/pixel` +
          `?x=${sample.mapX}&y=${sample.mapY}&crs=${encodeURIComponent(meta.local_crs!)}`,
        { headers: { Authorization: authorization } },
      )
    ).json();
    expect(pixel.in_bounds).toBe(true);
    // The resolved full-resolution pixel is the one containing that coordinate.
    const scaleCol = meta.source_width / meta.width;
    const scaleRow = meta.source_height / meta.height;
    expect(Math.abs(pixel.col + 0.5 - (col + 0.5) * scaleCol)).toBeLessThanOrEqual(scaleCol);
    expect(Math.abs(pixel.row + 0.5 - (row + 0.5) * scaleRow)).toBeLessThanOrEqual(scaleRow);

    // --- 8: horizontal flight stops at the footprint + 10% margin ---
    await page.keyboard.press("Equal");
    await page.keyboard.press("Equal");
    await page.keyboard.press("Equal");
    const edge = await holdAndSample(page, ["KeyW"], 2_500);
    const last = edge[edge.length - 1];
    const extentY = (meta.height - 1) * Math.abs(cellY);
    // Row 0's vertices (cell centres) are the north edge of the mesh footprint.
    const northLimit = meta.origin_y! + 0.5 * cellY + 0.1 * extentY;
    expect(last.mapY!).toBeCloseTo(northLimit, 6);
    expectClearanceNeverBelowMin(edge);

    // --- exit: flythrough ends, HUD disappears ---
    await page.evaluate(() => document.exitPointerLock());
    await expect(page.getByRole("button", { name: "Flythrough mode" })).toBeVisible({
      timeout: 5_000,
    });
    await expect(page.getByTestId("flight-hud")).toHaveCount(0);

    // --- 13: orbit + click inspection unchanged after flythrough ---
    // (Click inspection samples a visible scientific layer, as in the
    // Phase 13b spec.)
    await page
      .getByRole("button", { name: "Metric Elevation", exact: true })
      .locator("xpath=..")
      .getByRole("checkbox", { name: "Visible" })
      .click();
    await page.getByRole("button", { name: "Reset / fit camera" }).click();
    await page.waitForTimeout(500);
    const box = (await canvas.boundingBox())!;
    let sampled = false;
    // The same on-mesh positions the Phase 13b spec clicks after fitting.
    for (const [fx, fy] of [
      [0.7, 0.35],
      [0.5, 0.5],
      [0.6, 0.45],
      [0.55, 0.3],
    ]) {
      const valuePromise = page
        .waitForResponse((r) => r.url().includes("/visualization/value"), { timeout: 3_000 })
        .catch(() => null);
      await canvas.click({ position: { x: box.width * fx, y: box.height * fy } });
      const value = await valuePromise;
      if (value) {
        expect(value.status()).toBe(200);
        sampled = true;
        break;
      }
    }
    expect(sampled, "a 3D click on the mesh must sample after flythrough").toBe(true);
    await expect(page.getByText("Elevation:", { exact: false })).toBeVisible({ timeout: 10_000 });
    const orbitBefore = await canvas.screenshot();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.4, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(400);
    expect(Buffer.compare(orbitBefore, await canvas.screenshot())).not.toBe(0);

    assertNoErrors();
  });

  test("uncalibrated relative depth: labelled relative/unitless, never elevation; clearance holds", async ({
    page,
  }) => {
    const assertNoErrors = watchErrors(page);
    await registerAndLogin(page, "p17rel");
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(FIXTURES, "uncalibrated-source.jpg"),
    );
    await expect(page.getByText("valid", { exact: false }).first()).toBeVisible({ timeout: 20_000 });
    await runAnalysis(page, false);

    const { meta, grid, canvas } = await open3D(page);
    expect(meta.height_kind).toBe("relative_depth");
    expect(meta.is_georeferenced).toBe(false);

    await enterFlythrough(page, canvas);
    const entry = await readHud(page);
    expect(entry.mapX).toBeNull();
    expect(entry.pixelCol).not.toBeNull();
    expect(entry.groundText).toMatch(/^Relative depth below \(unitless, display grid\)/);
    const hudText = await page.getByTestId("flight-hud").innerText();
    expect(hudText).not.toMatch(/elevation/i);
    expect(entry.clearanceText).toMatch(/visual units \(relative, not a distance\)/);
    expect(await page.getByTestId("flight-hud-speed").innerText()).toMatch(/grid units\/s/);

    const descent = await holdAndSample(page, ["Shift", "KeyW"], 2_000);
    expectClearanceNeverBelowMin(descent);

    // HUD ground value == independent display-grid value at its position.
    const sample = await readHud(page);
    // Continuous source pixels -> fractional grid index (integer = cell centre).
    const col = (sample.pixelCol! * meta.width) / meta.source_width - 0.5;
    const row = (sample.pixelRow! * meta.height) / meta.source_height - 0.5;
    const expected = expectedGround(meta, grid, col, row);
    if (expected === null) {
      expect(sample.ground).toBeNull();
    } else {
      expect(sample.ground!).toBeCloseTo(expected, 6);
    }

    await page.evaluate(() => document.exitPointerLock());
    await expect(page.getByRole("button", { name: "Flythrough mode" })).toBeVisible({
      timeout: 5_000,
    });
    await expect(page.getByTestId("flight-hud")).toHaveCount(0);
    assertNoErrors();
  });
});
