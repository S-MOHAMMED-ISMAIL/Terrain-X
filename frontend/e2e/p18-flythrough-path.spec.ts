import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { ConsoleMessage, Page, Response } from "@playwright/test";

// P1-8 acceptance: waypoint flythrough — 2D (georeferenced, via the new
// terrain/local-coordinate endpoint) and 3D waypoints, deterministic
// playback along the flown polyline, WebM recording — against the ACTUAL
// running application. Everything is validated numerically through the
// waypoint list and the flythrough/playback HUDs (full-precision data-value
// attributes), the backend /measurements/pixel endpoint and an independent
// display-grid computation from /terrain/grid. Recording is validated by its
// WebM container structure, never by decoding the video.

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(__dirname, "fixtures");

interface TerrainMeta {
  artifact_id: string;
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

const num = (v: string | null) => (v === null || v === "" ? null : Number(v));

function uniqueEmail(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.floor(Math.random() * 1e6)}@example.com`;
}

function watchErrors(page: Page) {
  const consoleErrors: ConsoleMessage[] = [];
  const pageErrors: Error[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m);
  });
  page.on("pageerror", (e) => pageErrors.push(e));
  return () => {
    expect(pageErrors.map((e) => e.message)).toEqual([]);
    expect(consoleErrors.map((m) => m.text())).toEqual([]);
  };
}

async function setup(page: Page, prefix: string, files: string[]) {
  await page.goto("/register");
  await page.locator('input[type="email"]').fill(uniqueEmail(prefix));
  await page.locator('input[type="password"]').fill("P18PathPass123!");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/dashboard/, { timeout: 15_000 });
  await page.goto("/projects");
  const name = `P1-8 Path ${Date.now()}`;
  await page.getByPlaceholder("e.g. Coastal Flood Study").fill(name);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("link", { name }).click();
  await page.waitForURL(/\/projects\/[0-9a-f-]+/, { timeout: 15_000 });
  await page.locator('input[type="file"]').first().setInputFiles(path.join(FIXTURES, files[0]));
  await expect(page.getByText("valid", { exact: false }).first()).toBeVisible({ timeout: 20_000 });
  if (files[1]) {
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator('input[type="file"]').first().setInputFiles(path.join(FIXTURES, files[1]));
    await expect(page.getByText("valid", { exact: false }).nth(1)).toBeVisible({ timeout: 20_000 });
  }
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.locator("select").first().selectOption({ index: 1 });
  if (files[1]) {
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.locator("select").nth(1).selectOption({ index: 1 });
  }
  await page.getByRole("button", { name: "Start Analysis" }).click();
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({
    timeout: 120_000,
  });
  expect(await page.getByText("failed", { exact: true }).count()).toBe(0);
  await page.getByRole("button", { name: "Terrain", exact: true }).click();
  await page.locator("select").first().selectOption({ index: 1 });
  await expect(page.getByRole("button", { name: "3D Terrain" })).toBeVisible({ timeout: 15_000 });
}

async function waypointList(page: Page) {
  const items = page.getByTestId("waypoint-list").locator("li");
  const out = [];
  for (let i = 0; i < (await items.count()); i++) {
    const li = items.nth(i);
    out.push({
      mapX: num(await li.getAttribute("data-map-x")),
      mapY: num(await li.getAttribute("data-map-y")),
      pixelCol: num(await li.getAttribute("data-pixel-col")),
      pixelRow: num(await li.getAttribute("data-pixel-row")),
      localX: Number(await li.getAttribute("data-local-x")),
      localZ: Number(await li.getAttribute("data-local-z")),
    });
  }
  return out;
}

/** Clicks candidate positions (fractions of `target`'s box) until the list
 * has `count` waypoints; returns the responses to local-coordinate calls. */
async function addWaypoints(
  page: Page,
  target: ReturnType<Page["locator"]>,
  candidates: [number, number][],
  count: number,
) {
  const conversions: Response[] = [];
  const onResponse = (r: Response) => {
    if (r.url().includes("/terrain/local-coordinate")) conversions.push(r);
  };
  page.on("response", onResponse);
  for (const [fx, fy] of candidates) {
    if ((await page.getByTestId("waypoint-list").locator("li").count()) >= count) break;
    await target.scrollIntoViewIfNeeded();
    const box = (await target.boundingBox())!;
    const before = await page.getByTestId("waypoint-list").locator("li").count();
    await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
    await expect
      .poll(
        async () =>
          (await page.getByTestId("waypoint-list").locator("li").count()) > before ||
          (await page.getByTestId("waypoint-message").count()) > 0,
        { timeout: 10_000 },
      )
      .toBe(true);
  }
  page.off("response", onResponse);
  await expect(page.getByTestId("waypoint-list").locator("li")).toHaveCount(count);
  return conversions;
}

async function open3D(page: Page) {
  const metaPromise = page.waitForResponse((r) => r.url().includes("/terrain/metadata"));
  await page.getByRole("button", { name: "3D Terrain" }).click();
  const metaResponse = await metaPromise;
  const meta = (await metaResponse.json()) as TerrainMeta;
  const authorization = (await metaResponse.request().allHeaders())["authorization"];
  const gridResponse = await page.request.get(
    metaResponse.url().replace("/terrain/metadata", "/terrain/grid"),
    { headers: { Authorization: authorization } },
  );
  const bytes = await gridResponse.body();
  const grid = new Float32Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
  expect(grid.length).toBe(meta.width * meta.height);
  const canvas = page.locator("canvas:not([data-decorative-canvas])").first();
  await expect(canvas).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Loading terrain height grid")).toHaveCount(0, { timeout: 20_000 });
  return { meta, grid, canvas, metaUrl: metaResponse.url(), authorization };
}

/** Independent display-grid value on the mesh's own triangles. */
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

/** One atomic snapshot of the playback + flight HUDs (a single DOM read, so
 * every value comes from the same rendered frame while playback runs). */
async function readPlayback(page: Page) {
  const raw = await page.evaluate(() => {
    const el = (id: string) => document.querySelector(`[data-testid="${id}"]`);
    const attr = (id: string, name: string) => el(id)?.getAttribute(name) ?? null;
    return {
      state: attr("path-status", "data-value"),
      progress: attr("path-progress", "data-value"),
      elapsed: attr("path-time", "data-value"),
      targetClearance: attr("path-clearance", "data-value"),
      clearance: attr("flight-hud-clearance", "data-value"),
      ground: attr("flight-hud-ground", "data-value"),
      groundText: (el("flight-hud-ground") as HTMLElement | null)?.innerText ?? "",
      mapX: attr("flight-hud-position", "data-map-x"),
      mapY: attr("flight-hud-position", "data-map-y"),
      pixelCol: attr("flight-hud-position", "data-pixel-col"),
      pixelRow: attr("flight-hud-position", "data-pixel-row"),
    };
  });
  return {
    state: raw.state,
    progress: Number(raw.progress),
    elapsed: Number(raw.elapsed),
    targetClearance: num(raw.targetClearance),
    clearance: num(raw.clearance),
    ground: num(raw.ground),
    groundText: raw.groundText,
    mapX: num(raw.mapX),
    mapY: num(raw.mapY),
    pixelCol: num(raw.pixelCol),
    pixelRow: num(raw.pixelRow),
  };
}

/** Samples the playback HUD every ~100 ms until the path finishes. */
async function sampleUntilFinished(page: Page, timeoutMs: number) {
  const samples = [];
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    await page.waitForTimeout(100);
    const s = await readPlayback(page);
    samples.push(s);
    if (s.state === "finished") break;
  }
  return samples;
}

function assertPlaybackSamples(samples: Awaited<ReturnType<typeof readPlayback>>[]) {
  let last = -1;
  for (const s of samples) {
    expect(["playing", "finished"]).toContain(s.state);
    expect(s.progress).toBeGreaterThanOrEqual(last);
    last = s.progress;
    if (s.clearance !== null && s.targetClearance !== null) {
      // Height above the rendered surface never drops below the path's target clearance.
      expect(s.clearance, JSON.stringify(s)).toBeGreaterThanOrEqual(s.targetClearance - 1e-4);
    }
  }
}

function webmStructure(bytes: Buffer) {
  const find = (needle: number[]) => {
    outer: for (let i = 0; i + needle.length <= bytes.length; i++) {
      for (let j = 0; j < needle.length; j++) if (bytes[i + j] !== needle[j]) continue outer;
      return i;
    }
    return -1;
  };
  const docType = find([0x42, 0x82]); // EBML DocType element
  return {
    ebml: bytes.subarray(0, 4).equals(Buffer.from([0x1a, 0x45, 0xdf, 0xa3])),
    docTypeWebm:
      docType >= 0 && docType < 64 && bytes.subarray(docType, docType + 8).toString("latin1").includes("webm"),
    cluster: find([0x1f, 0x43, 0xb6, 0x75]) > 0,
    size: bytes.length,
  };
}

test.describe("P1-8: waypoint flythrough with playback and recording", () => {
  test("off-meridian DSM: 2D + 3D waypoints, clearance-safe deterministic playback, controls, recording, path JSON", async ({
    page,
  }) => {
    const assertNoErrors = watchErrors(page);
    await setup(page, "p18dsm", ["offmeridian-source.tif", "offmeridian-dem.tif"]);

    // --- 2D waypoints (georeferenced -> terrain/local-coordinate) ---
    await page.getByRole("button", { name: "Add waypoints" }).click();
    const overlay = page.locator(".leaflet-image-layer").last();
    await expect(overlay).toBeVisible({ timeout: 10_000 });
    const conversions = await addWaypoints(
      page,
      overlay,
      [
        [0.3, 0.7],
        [0.7, 0.55],
        [0.35, 0.3],
        [0.6, 0.35],
      ],
      3,
    );
    expect(conversions.length).toBeGreaterThanOrEqual(3);
    let list = await waypointList(page);
    for (let i = 0; i < 3; i++) {
      const response = conversions[conversions.length - 3 + i];
      const body = await response.json();
      expect(body.in_footprint).toBe(true);
      expect(list[i].mapX).toBe(body.map_x);
      expect(list[i].mapY).toBe(body.map_y);
      // The converted local coordinate resolves to the same full-resolution
      // pixel as the clicked lng/lat itself (backend-authoritative).
      const url = new URL(response.url());
      const lng = url.searchParams.get("lng");
      const lat = url.searchParams.get("lat");
      const pixelBase = response.url().split("/visualization/")[0] + "/measurements/pixel";
      const authorization = (await response.request().allHeaders())["authorization"];
      const viaLocal = await (
        await page.request.get(
          `${pixelBase}?x=${body.map_x}&y=${body.map_y}&crs=${encodeURIComponent(body.local_crs)}`,
          { headers: { Authorization: authorization } },
        )
      ).json();
      const viaWgs84 = await (
        await page.request.get(`${pixelBase}?x=${lng}&y=${lat}&crs=EPSG:4326`, {
          headers: { Authorization: authorization },
        })
      ).json();
      expect([viaLocal.row, viaLocal.col]).toEqual([viaWgs84.row, viaWgs84.col]);
      expect(viaLocal.in_bounds).toBe(true);
    }

    // --- 3D: a 4th waypoint by clicking the terrain ---
    const { meta, grid, canvas } = await open3D(page);
    expect(meta.height_kind).toBe("elevation");
    await addWaypoints(
      page,
      canvas,
      [
        [0.7, 0.35],
        [0.6, 0.45],
        [0.5, 0.5],
        [0.55, 0.3],
        [0.45, 0.6],
      ],
      4,
    );
    list = await waypointList(page);
    // Local <-> map consistency for every waypoint (3D: map = origin + local).
    for (const w of list) {
      expect(w.mapX!).toBeCloseTo(meta.origin_x! + w.localX, 6);
      expect(w.mapY!).toBeCloseTo(meta.origin_y! + w.localZ, 6);
    }
    await page.getByRole("button", { name: "Stop adding waypoints" }).click();
    await expect(page.getByTestId("path-summary")).toContainText("Path ready");

    // --- playback at 4x: progress monotonic, clearance never below target,
    // HUD ground == independent display-grid value, ends on the last waypoint ---
    await page.getByLabel("Playback speed").selectOption("4");
    await page.getByRole("button", { name: "Play path" }).click();
    await expect(page.getByTestId("playback-hud")).toBeVisible({ timeout: 5_000 });
    const run = await sampleUntilFinished(page, 60_000);
    assertPlaybackSamples(run);
    const final = run[run.length - 1];
    expect(final.state).toBe("finished");
    expect(final.progress).toBe(1);
    expect(final.mapX!).toBeCloseTo(list[3].mapX!, 6);
    expect(final.mapY!).toBeCloseTo(list[3].mapY!, 6);
    let checked = 0;
    for (const s of run) {
      if (s.mapX === null || s.ground === null) continue;
      // Fractional grid index: integer = the centre of that cell (D1).
      const col = (s.mapX - meta.origin_x!) / meta.cell_size_x! - 0.5;
      const row = (s.mapY! - meta.origin_y!) / meta.cell_size_y! - 0.5;
      const expected = expectedGround(meta, grid, col, row);
      expect(expected).not.toBeNull();
      expect(s.ground).toBeCloseTo(expected!, 6);
      checked++;
    }
    expect(checked).toBeGreaterThan(3);
    expect(final.groundText).toMatch(/^Ground elevation/);

    // --- restart, pause holds, resume advances (at 0.5x so the restart is
    // observable before the short path ends) ---
    await page.getByLabel("Playback speed").selectOption("0.5");
    await page.getByRole("button", { name: "Restart" }).click();
    await page.getByRole("button", { name: "Pause" }).click();
    await expect.poll(async () => (await readPlayback(page)).state).toBe("paused");
    const paused1 = await readPlayback(page);
    expect(paused1.progress).toBeLessThan(0.5);
    await page.waitForTimeout(600);
    const paused2 = await readPlayback(page);
    expect(paused2.progress).toBe(paused1.progress);
    expect(paused2.elapsed).toBe(paused1.elapsed);
    await page.getByRole("button", { name: "Resume" }).click();
    await expect.poll(async () => (await readPlayback(page)).progress).toBeGreaterThan(paused2.progress);

    // --- stop: playback ends, orbit view restored ---
    await page.getByRole("button", { name: "Stop" }).click();
    await expect(page.getByTestId("playback-hud")).toHaveCount(0);
    await expect(page.getByTestId("flight-hud")).toHaveCount(0);

    // --- path JSON download matches the waypoints ---
    const jsonDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download path (JSON)" }).click();
    const pathJson = JSON.parse(readFileSync(await (await jsonDownload).path(), "utf-8"));
    expect(pathJson.local_crs).toBe(meta.local_crs);
    expect(pathJson.waypoints).toEqual(list.map((w) => ({ map_x: w.mapX, map_y: w.mapY })));

    // --- recording: a valid WebM container (not decoded) ---
    const videoDownload = page.waitForEvent("download", { timeout: 90_000 });
    await page.getByRole("button", { name: "Record flythrough (WebM)" }).click();
    const recorded = await sampleUntilFinished(page, 60_000);
    assertPlaybackSamples(recorded);
    const download = await videoDownload;
    expect(download.suggestedFilename()).toBe(`terrainx-flythrough-${meta.artifact_id}.webm`);
    const webm = webmStructure(readFileSync(await download.path()));
    expect(webm.ebml).toBe(true);
    expect(webm.docTypeWebm).toBe(true);
    expect(webm.cluster).toBe(true);
    expect(webm.size).toBeGreaterThan(4096);
    await expect(page.getByTestId("playback-message")).toHaveText("Recording saved.");
    await page.getByRole("button", { name: "Stop" }).click();

    // --- regressions: 3D click inspection + orbit after playback ---
    await page
      .getByRole("button", { name: "Metric Elevation", exact: true })
      .locator("xpath=..")
      .getByRole("checkbox", { name: "Visible" })
      .click();
    await page.getByRole("button", { name: "Reset / fit camera" }).click();
    await page.waitForTimeout(500);
    const cbox = (await canvas.boundingBox())!;
    let sampled = false;
    for (const [fx, fy] of [
      [0.7, 0.35],
      [0.5, 0.5],
      [0.6, 0.45],
      [0.55, 0.3],
    ]) {
      const value = page
        .waitForResponse((r) => r.url().includes("/visualization/value"), { timeout: 3_000 })
        .catch(() => null);
      await canvas.click({ position: { x: cbox.width * fx, y: cbox.height * fy } });
      const response = await value;
      if (response) {
        expect(response.status()).toBe(200);
        sampled = true;
        break;
      }
    }
    expect(sampled).toBe(true);
    const beforeOrbit = await canvas.screenshot();
    await page.mouse.move(cbox.x + cbox.width * 0.5, cbox.y + cbox.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(cbox.x + cbox.width * 0.2, cbox.y + cbox.height * 0.4, { steps: 8 });
    await page.mouse.up();
    await page.waitForTimeout(400);
    expect(Buffer.compare(beforeOrbit, await canvas.screenshot())).not.toBe(0);

    assertNoErrors();
  });

  test("uncalibrated relative depth: pixel waypoints, relative/unitless playback, recording unsupported", async ({
    page,
  }) => {
    // A browser without MediaRecorder: the control must be disabled with the
    // real reason, and nothing may be recorded or downloaded.
    await page.addInitScript(() => {
      delete (window as unknown as { MediaRecorder?: unknown }).MediaRecorder;
    });
    const assertNoErrors = watchErrors(page);
    await setup(page, "p18rel", ["uncalibrated-source.jpg"]);

    let conversions = 0;
    page.on("request", (r) => {
      if (r.url().includes("/terrain/local-coordinate")) conversions++;
    });
    await page.getByRole("button", { name: "Add waypoints" }).click();
    const overlay = page.locator(".leaflet-image-layer").last();
    await expect(overlay).toBeVisible({ timeout: 10_000 });
    await addWaypoints(
      page,
      overlay,
      [
        [0.2, 0.85],
        [0.8, 0.8],
        [0.5, 0.9],
        [0.3, 0.75],
        [0.7, 0.9],
      ],
      2,
    );
    expect(conversions).toBe(0); // not georeferenced: pixel mapping, no conversion call
    await page.getByRole("button", { name: "Stop adding waypoints" }).click();

    const { meta, grid } = await open3D(page);
    expect(meta.height_kind).toBe("relative_depth");
    const list = await waypointList(page);
    for (const w of list) {
      expect(w.mapX).toBeNull();
      expect(w.localX).toBeCloseTo(w.pixelCol! * (meta.width / meta.source_width), 9);
      expect(w.localZ).toBeCloseTo(w.pixelRow! * (meta.height / meta.source_height), 9);
    }
    await expect(page.getByTestId("path-summary")).toContainText("Path ready");
    await expect(page.getByTestId("path-summary")).toContainText("visual units");
    await expect(page.getByRole("button", { name: "Record flythrough (WebM)" })).toBeDisabled();
    await expect(page.getByTestId("recording-unsupported")).toHaveText(
      "Recording is not supported in this browser (no MediaRecorder).",
    );

    let downloads = 0;
    page.on("download", () => downloads++);
    await page.getByLabel("Playback speed").selectOption("4");
    await page.getByRole("button", { name: "Play path" }).click();
    const run = await sampleUntilFinished(page, 60_000);
    assertPlaybackSamples(run);
    expect(run[run.length - 1].state).toBe("finished");
    expect(await page.getByTestId("flight-hud").innerText()).not.toMatch(/elevation/i);
    expect(run[run.length - 1].groundText).toMatch(/^Relative depth below \(unitless, display grid\)/);
    for (const s of run) {
      if (s.pixelCol === null || s.ground === null) continue;
      // Continuous source pixels -> fractional grid index (integer = cell centre).
      const col = (s.pixelCol * meta.width) / meta.source_width - 0.5;
      const row = (s.pixelRow! * meta.height) / meta.source_height - 0.5;
      const expected = expectedGround(meta, grid, col, row);
      expect(expected).not.toBeNull();
      expect(s.ground).toBeCloseTo(expected!, 6);
    }
    await page.getByRole("button", { name: "Stop" }).click();
    await expect(page.getByTestId("playback-hud")).toHaveCount(0);
    expect(downloads).toBe(0);
    assertNoErrors();
  });
});
