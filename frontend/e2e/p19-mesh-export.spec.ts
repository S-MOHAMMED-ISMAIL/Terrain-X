import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { ConsoleMessage, Page, Request } from "@playwright/test";

// P1-9 acceptance: 3D terrain mesh export (GLB) against the ACTUAL running
// application. The downloaded GLB is parsed twice — by an independent byte
// parser in this test and by three.js GLTFLoader in the page — and every
// vertex is checked against the terrain grid (/terrain/metadata +
// /terrain/grid, fetched independently): cell-centre coordinates, raw grid
// values as heights (no exaggeration), NoData/sky as holes, triangle counts,
// CRS metadata, texture behaviour and relative-depth semantics.

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

// ---------------------------------------------------------------------------
// Independent GLB parser (glTF 2.0 spec)
// ---------------------------------------------------------------------------

interface ParsedGlb {
  json: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  positions: Float32Array;
  indices: Uint32Array;
  image: Buffer | null;
  hasUv: boolean;
}

function parseGlb(bytes: Buffer): ParsedGlb {
  expect(bytes.readUInt32LE(0)).toBe(0x46546c67);
  expect(bytes.readUInt32LE(4)).toBe(2);
  expect(bytes.readUInt32LE(8)).toBe(bytes.length);
  const jsonLength = bytes.readUInt32LE(12);
  expect(bytes.readUInt32LE(16)).toBe(0x4e4f534a);
  const json = JSON.parse(bytes.subarray(20, 20 + jsonLength).toString("utf-8"));
  const binStart = 20 + jsonLength;
  const binLength = bytes.readUInt32LE(binStart);
  expect(bytes.readUInt32LE(binStart + 4)).toBe(0x004e4942);
  const bin = bytes.subarray(binStart + 8, binStart + 8 + binLength);
  const read = (index: number) => {
    const acc = json.accessors[index];
    const view = json.bufferViews[acc.bufferView];
    const width = { SCALAR: 1, VEC2: 2, VEC3: 3 }[acc.type as "SCALAR" | "VEC2" | "VEC3"];
    const offset = (view.byteOffset ?? 0) + (acc.byteOffset ?? 0);
    const n = acc.count * width;
    const out = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      if (acc.componentType === 5126) out[i] = bin.readFloatLE(offset + i * 4);
      else if (acc.componentType === 5125) out[i] = bin.readUInt32LE(offset + i * 4);
      else if (acc.componentType === 5123) out[i] = bin.readUInt16LE(offset + i * 2);
      else throw new Error(`component ${acc.componentType}`);
    }
    return out;
  };
  const prim = json.meshes[0].primitives[0];
  const image = json.images
    ? bin.subarray(
        json.bufferViews[json.images[0].bufferView].byteOffset,
        json.bufferViews[json.images[0].bufferView].byteOffset +
          json.bufferViews[json.images[0].bufferView].byteLength,
      )
    : null;
  return {
    json,
    positions: Float32Array.from(read(prim.attributes.POSITION)),
    indices: Uint32Array.from(read(prim.indices)),
    image,
    hasUv: "TEXCOORD_0" in prim.attributes,
  };
}

function validQuads(meta: TerrainMeta, grid: Float32Array): number {
  let n = 0;
  for (let r = 0; r < meta.height - 1; r++) {
    for (let c = 0; c < meta.width - 1; c++) {
      const i = r * meta.width + c;
      if (
        [grid[i], grid[i + 1], grid[i + meta.width], grid[i + meta.width + 1]].every(Number.isFinite)
      ) {
        n++;
      }
    }
  }
  return n;
}

// ---------------------------------------------------------------------------

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
  await page.locator('input[type="password"]').fill("P19MeshPass123!");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/dashboard/, { timeout: 15_000 });
  await page.goto("/projects");
  const name = `P1-9 Mesh ${Date.now()}`;
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
  await expect(page.getByTestId("mesh-export-card")).toBeVisible({ timeout: 15_000 });
}

/** Clicks Export and returns the downloaded bytes and the request made. */
async function exportMesh(page: Page) {
  const requestPromise = page.waitForRequest((r) => r.url().includes("/terrain/mesh.glb"));
  const downloadPromise = page.waitForEvent("download", { timeout: 30_000 });
  await page.getByRole("button", { name: "Export 3D mesh (GLB)" }).click();
  const request: Request = await requestPromise;
  const download = await downloadPromise;
  await expect(page.getByTestId("mesh-export-summary")).toBeVisible({ timeout: 10_000 });
  return { bytes: readFileSync(await download.path()), request, filename: download.suggestedFilename() };
}

async function terrainFor(page: Page, request: Request) {
  const base = request.url().split("/visualization/terrain/mesh.glb")[0];
  const authorization = (await request.allHeaders())["authorization"];
  const headers = { Authorization: authorization };
  const meta = (await (
    await page.request.get(`${base}/visualization/terrain/metadata`, { headers })
  ).json()) as TerrainMeta;
  const raw = await (await page.request.get(`${base}/visualization/terrain/grid`, { headers })).body();
  const grid = new Float32Array(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength));
  return { base, headers, meta, grid };
}

/** three.js GLTFLoader, in the page, as a second independent parser. */
async function loadWithGltfLoader(page: Page, bytes: Buffer) {
  return page.evaluate(async (b64) => {
    const { GLTFLoader } = await import(
      /* @vite-ignore */ "/node_modules/three/examples/jsm/loaders/GLTFLoader.js"
    );
    const binary = Uint8Array.from(atob(b64), (ch) => ch.charCodeAt(0));
    const gltf = await new GLTFLoader().parseAsync(binary.buffer, "");
    let positions = 0;
    let indices = 0;
    let first: number[] = [];
    let textured = false;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    gltf.scene.traverse((o: any) => {
      if (o.isMesh) {
        positions += o.geometry.attributes.position.count;
        indices += o.geometry.index.count;
        first = Array.from(o.geometry.attributes.position.array.slice(0, 3));
        textured = !!o.material.map;
      }
    });
    return { positions, indices, first, textured, extras: gltf.asset.extras };
  }, bytes.toString("base64"));
}

test.describe("P1-9: 3D terrain mesh export (GLB)", () => {
  test("off-meridian calibrated DSM: cell centres, exact heights, counts, CRS, texture, limits, no exaggeration", async ({
    page,
  }) => {
    const assertNoErrors = watchErrors(page);
    await setup(page, "p19dsm", ["offmeridian-source.tif", "offmeridian-dem.tif"]);

    // --- 256 export ---
    const first = await exportMesh(page);
    expect(first.request.url()).toContain("resolution=256");
    const glb = parseGlb(first.bytes);
    const { base, headers, meta, grid } = await terrainFor(page, first.request);
    const ex = glb.json.asset.extras.terrainx;
    expect(first.filename).toBe(`terrainx-terrain-${ex.artifact_id}-256.glb`);
    expect(meta.height_kind).toBe("elevation");
    expect(ex.height_kind).toBe("elevation");
    expect(ex.display_exaggeration_applied).toBe(false);
    expect(ex.local_crs).toBe(meta.local_crs);
    expect(ex.local_crs_wkt).toContain("UTM zone 33N");
    expect(ex.axis_mapping).toEqual({ map_x: "origin_x + x", map_y: "origin_y - z", value: "y" });
    const cellX = meta.cell_size_x!;
    const cellY = meta.cell_size_y!;
    expect(ex.origin_x).toBeCloseTo(meta.origin_x! + 0.5 * cellX, 6);
    expect(ex.origin_y).toBeCloseTo(meta.origin_y! + 0.5 * cellY, 6);

    // Every vertex: integer cell, cell-centre map coordinate, exact grid
    // value, never over a non-finite (NoData) cell. Mismatches are counted
    // and asserted once (one expect per vertex is far too slow).
    const nVerts = glb.positions.length / 3;
    const seen = new Set<number>();
    const bad = { offGrid: 0, position: 0, height: 0, overNoData: 0 };
    for (let v = 0; v < nVerts; v++) {
      const x = glb.positions[v * 3];
      const y = glb.positions[v * 3 + 1];
      const z = glb.positions[v * 3 + 2];
      const col = x / cellX;
      const row = -z / cellY;
      const c = Math.round(col);
      const r = Math.round(row);
      if (Math.abs(col - c) > 1e-3 || Math.abs(row - r) > 1e-3) bad.offGrid++;
      if (
        Math.abs(ex.origin_x + x - (meta.origin_x! + (c + 0.5) * cellX)) > 1e-2 ||
        Math.abs(ex.origin_y - z - (meta.origin_y! + (r + 0.5) * cellY)) > 1e-2
      ) {
        bad.position++;
      }
      if (y !== grid[r * meta.width + c]) bad.height++; // raw value, bit-exact
      if (!Number.isFinite(grid[r * meta.width + c])) bad.overNoData++;
      seen.add(r * meta.width + c);
    }
    expect(bad).toEqual({ offGrid: 0, position: 0, height: 0, overNoData: 0 });
    expect(seen.size).toBe(nVerts);
    expect(glb.indices.length / 3).toBe(2 * validQuads(meta, grid));
    expect(ex.triangle_count).toBe(glb.indices.length / 3);

    // Off-meridian round trip for representative vertices through the backend.
    for (const v of [0, Math.floor(nVerts / 2), nVerts - 1]) {
      const mx = ex.origin_x + glb.positions[v * 3];
      const my = ex.origin_y - glb.positions[v * 3 + 2];
      const pixel = await (
        await page.request.get(
          `${base}/measurements/pixel?x=${mx}&y=${my}&crs=${encodeURIComponent(meta.local_crs!)}`,
          { headers },
        )
      ).json();
      expect(pixel.in_bounds).toBe(true);
      const c = Math.round(glb.positions[v * 3] / cellX);
      const r = Math.round(-glb.positions[v * 3 + 2] / cellY);
      expect([pixel.row, pixel.col]).toEqual([
        r * (meta.source_height / meta.height),
        c * (meta.source_width / meta.width),
      ]);
    }

    // Texture embedded (projected source, sizes match): a real PNG of the source size.
    expect(ex.texture).toBe("embedded");
    expect(glb.hasUv).toBe(true);
    expect(glb.image!.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))).toBe(true);
    expect(glb.image!.readUInt32BE(16)).toBe(meta.source_width);
    expect(glb.image!.readUInt32BE(20)).toBe(meta.source_height);

    // three.js GLTFLoader agrees.
    const loaded = await loadWithGltfLoader(page, first.bytes);
    expect(loaded.positions).toBe(nVerts);
    expect(loaded.indices).toBe(glb.indices.length);
    expect(loaded.first).toEqual(Array.from(glb.positions.slice(0, 3)));
    expect(loaded.textured).toBe(true);
    expect(loaded.extras.terrainx.height_kind).toBe("elevation");

    // --- 512 export; 1024 and other resolutions rejected ---
    await page.getByLabel("Mesh resolution").selectOption("512");
    const at512 = await exportMesh(page);
    expect(at512.request.url()).toContain("resolution=512");
    expect(parseGlb(at512.bytes).json.asset.extras.terrainx.resolution_requested).toBe(512);
    await expect(page.getByLabel("Mesh resolution").locator("option")).toHaveText(["256 px", "512 px"]);
    for (const bad of [1024, 128]) {
      const resp = await page.request.get(
        `${base}/visualization/terrain/mesh.glb?resolution=${bad}`,
        { headers },
      );
      expect(resp.status()).toBe(422);
    }

    // --- display exaggeration never enters the export ---
    await page.getByLabel("Mesh resolution").selectOption("256");
    await page.getByRole("button", { name: "3D Terrain" }).click();
    await expect(page.getByText("Loading terrain height grid")).toHaveCount(0, { timeout: 20_000 });
    const exaggerationLabel = page.getByText(/^Vertical exaggeration: /);
    const before = await exaggerationLabel.innerText();
    const slider = exaggerationLabel.locator('input[type="range"]');
    await slider.fill("4");
    await expect(exaggerationLabel).toHaveText("Vertical exaggeration: 4.0×");
    expect(before).not.toBe("Vertical exaggeration: 4.0×");
    const again = await exportMesh(page);
    expect(again.bytes.equals(first.bytes)).toBe(true);

    assertNoErrors();
  });

  test("uncalibrated relative depth: raw unitless values, pixel units, NoData/sky invariant, never elevation", async ({
    page,
  }) => {
    const assertNoErrors = watchErrors(page);
    await setup(page, "p19rel", ["uncalibrated-source.jpg"]);
    const out = await exportMesh(page);
    const glb = parseGlb(out.bytes);
    const { meta, grid } = await terrainFor(page, out.request);
    const ex = glb.json.asset.extras.terrainx;
    expect(meta.height_kind).toBe("relative_depth");
    expect(ex.height_kind).toBe("relative_depth");
    expect(ex.vertical_units).toBe("unitless");
    expect(ex.physical_height).toBe(false);
    expect(ex.horizontal_vertical_units_comparable).toBe(false);
    expect(ex.vertical_semantics).toContain("Not a physical height");
    expect(JSON.stringify(ex).toLowerCase()).not.toMatch(/elevation|metre|meter/);
    expect(ex.georeferenced).toBe(false);
    expect(ex.axis_mapping).toEqual({ pixel_col: "origin_x + x", pixel_row: "origin_y + z", value: "y" });
    expect(ex.sky_mask_applied).toBe(true);
    await expect(page.getByTestId("mesh-export-summary")).toContainText("relative depth in unitless");

    const stepX = meta.source_width / meta.width;
    const stepZ = meta.source_height / meta.height;
    const nVerts = glb.positions.length / 3;
    const bad = { height: 0, overNoData: 0 };
    for (let v = 0; v < nVerts; v++) {
      const c = Math.round(glb.positions[v * 3] / stepX);
      const r = Math.round(glb.positions[v * 3 + 2] / stepZ);
      if (glb.positions[v * 3 + 1] !== grid[r * meta.width + c]) bad.height++; // raw, no gamma
      if (!Number.isFinite(grid[r * meta.width + c])) bad.overNoData++; // never over sky/NoData
    }
    expect(bad).toEqual({ height: 0, overNoData: 0 });
    // Triangles exist exactly for the quads whose four cells are finite.
    expect(glb.indices.length / 3).toBe(2 * validQuads(meta, grid));

    const loaded = await loadWithGltfLoader(page, out.bytes);
    expect(loaded.positions).toBe(nVerts);
    expect(loaded.extras.terrainx.vertical_units).toBe("unitless");
    assertNoErrors();
  });
});
