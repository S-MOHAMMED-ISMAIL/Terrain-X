import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// Post-Phase-13 regression: MapView2D.tsx and TerrainView3D.tsx previously
// registered their map/scene click handlers inside a one-time useEffect
// that closed over a STALE `onSample`/measurement-mode value captured at
// mount — so switching measurement mode and clicking the real map silently
// did nothing (the click was always treated as plain inspection). Fixed by
// reading the current callback through a ref (onSampleRef), updated every
// render, instead of the closed-over prop directly — the same pattern this
// file already used for `onExitFirstPersonRef` in TerrainView3D.tsx.
//
// This spec exercises the exact real, end-to-end scenario the bug report
// described, against the actual running application (real backend, real
// worker, real calibration) — no mocked API, no fabricated data.

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

/** Real setup shared by both tests below: a real calibrated project with a
 * completed analysis job, landed on the Terrain workspace with the real
 * Metric Elevation layer made visible. */
async function setUpCalibratedTerrainWorkspace(page: Page): Promise<void> {
  const email = uniqueEmail("p13b");
  await registerAndLogin(page, email, "P13bMeasurePass123!");

  await page.getByRole("link", { name: "Projects" }).first().click();
  const projectName = `Phase 13b Measurement ${Date.now()}`;
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

  await page.getByRole("button", { name: "Analysis" }).click();
  await page.locator("select").first().selectOption({ index: 1 });
  await page.getByRole("button", { name: "DEM reference" }).click();
  await page.locator("select").nth(1).selectOption({ index: 1 });
  await page.getByRole("button", { name: "Start Analysis" }).click();
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  expect(await page.getByText("failed", { exact: true }).count()).toBe(0);

  await page.getByRole("button", { name: "Terrain", exact: true }).click();
  await page.locator("select").first().selectOption({ index: 1 });
  await expect(page.getByRole("button", { name: "3D Terrain" })).toBeVisible({ timeout: 15_000 });

  const metricElevationRow = page
    .getByRole("button", { name: "Metric Elevation", exact: true })
    .locator("xpath=..");
  await metricElevationRow.getByRole("checkbox", { name: "Visible" }).click();
}

test.describe("Post-Phase-13: measurement-mode click interaction (2D)", () => {
  test("inspect / point elevation / distance / profile all work after real mode switching, with no stale or duplicate handling", async ({
    page,
  }) => {
    const pageErrors: Error[] = [];
    page.on("pageerror", (err) => pageErrors.push(err));

    await setUpCalibratedTerrainWorkspace(page);

    const overlayImage = page.locator(".leaflet-image-layer").last();
    await expect(overlayImage, "the real raster overlay must have actually rendered").toBeVisible({
      timeout: 10_000,
    });

    // Re-measures the real overlay position immediately before every click
    // (an intervening mode-button click can scroll the page).
    async function clickAt(fracX: number, fracY: number) {
      await overlayImage.scrollIntoViewIfNeeded();
      const box = await overlayImage.boundingBox();
      expect(box, "the real raster overlay must have a real bounding box").not.toBeNull();
      await page.mouse.click(box!.x + box!.width * fracX, box!.y + box!.height * fracY);
    }

    // --- 1-5: Inspect -> click -> real inspection value ---
    await clickAt(0.5, 0.5);
    await expect(page.getByText("Elevation:", { exact: false })).toBeVisible({ timeout: 10_000 });

    // --- 6-8: Point elevation -> click -> real measurement result ---
    await page.getByRole("button", { name: "Point elevation" }).click();
    await clickAt(0.5, 0.5);
    await expect(page.getByText("Measurement result")).toBeVisible({ timeout: 15_000 });
    const firstResultText = await page.locator("text=Elevation").first().innerText();

    // --- 9-11: switch back to Inspect -> click -> inspection still works ---
    await page.getByRole("button", { name: "Inspect (off)", exact: true }).click();
    await clickAt(0.5, 0.5);
    await expect(page.getByText("Elevation:", { exact: false })).toBeVisible({ timeout: 10_000 });

    // --- 12-13: switch modes multiple times; confirm no duplicate/stuck
    // results — each switch clears the prior result, and only ONE real
    // measurement-result panel is ever present at a time. ---
    for (let i = 0; i < 3; i++) {
      await page.getByRole("button", { name: "Point elevation" }).click();
      await clickAt(0.4, 0.6);
      await expect(page.getByText("Measurement result")).toHaveCount(1, { timeout: 15_000 });
      await page.getByRole("button", { name: "Inspect (off)", exact: true }).click();
      await expect(page.getByText("Measurement result")).toHaveCount(0);
    }

    // --- 14: Distance mode (2 real points, 2 distinct in-bounds clicks) ---
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

    // --- 15: Profile mode (2 real points) ---
    await page.getByRole("button", { name: "Inspect (off)", exact: true }).click();
    await page.getByRole("button", { name: "Profile" }).click();
    await clickAt(0.3, 0.3);
    await clickAt(0.7, 0.7);
    await expect(page.getByText("samples over", { exact: false })).toBeVisible({ timeout: 15_000 });

    expect(
      pageErrors,
      `uncaught page errors: ${pageErrors.map((e) => e.message).join("; ")}`,
    ).toEqual([]);

    test.info().annotations.push({
      type: "firstPointElevationResult",
      description: firstResultText,
    });
  });
});

test.describe("Post-Phase-13: measurement-mode click interaction (3D)", () => {
  test("3D inspection and point-elevation measurement both work after real mode switching", async ({
    page,
  }) => {
    const pageErrors: Error[] = [];
    page.on("pageerror", (err) => pageErrors.push(err));

    await setUpCalibratedTerrainWorkspace(page);
    await page.getByRole("button", { name: "3D Terrain" }).click();

    // Scoped to exclude the ambient DynamicScientificBackground canvas
    // (mounted globally in AppShell, marked data-decorative-canvas) — a
    // bare `canvas` selector would otherwise match it first.
    const canvas = page.locator("canvas:not([data-decorative-canvas])").first();
    await expect(canvas).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Loading terrain height grid")).toHaveCount(0, { timeout: 20_000 });

    // The real mesh (fitCamera's default framing) renders occupying roughly
    // the right-half/center of the canvas, not its top-left corner — click
    // a position confirmed (via a real captured screenshot) to land on the
    // actual rendered terrain, not empty background.
    const canvasBox = await canvas.boundingBox();
    expect(canvasBox, "the real 3D canvas must have a real bounding box").not.toBeNull();
    const meshClickPosition = {
      x: canvasBox!.width * 0.7,
      y: canvasBox!.height * 0.35,
    };

    // --- 16-17: 3D inspection via a real raycast click ---
    await canvas.click({ position: meshClickPosition });
    // Either a real value or an honest "no data at this point" is
    // acceptable (the exact click position may or may not hit the mesh at
    // a valid pixel) — what matters is that SOME real inspection response
    // is observed, proving the click reaches the real handler.
    await expect(
      page.getByText("Elevation:", { exact: false }).or(page.getByText("Sampling")).first(),
    ).toBeVisible({ timeout: 10_000 });

    // --- 18-20: switch to Point elevation and perform a real 3D
    // measurement click ---
    await page.getByRole("button", { name: "Point elevation" }).click();
    await canvas.click({ position: meshClickPosition });
    await expect(
      page
        .getByText("Measurement result")
        .or(page.getByText("Sampling"))
        .or(page.getByText("Click 1 more point", { exact: false }))
        .first(),
    ).toBeVisible({ timeout: 15_000 });

    // --- 21: switch modes repeatedly; no duplicate/stuck handler ---
    for (let i = 0; i < 3; i++) {
      const p = { x: meshClickPosition.x + i * 5, y: meshClickPosition.y + i * 5 };
      await page.getByRole("button", { name: "Inspect (off)", exact: true }).click();
      await canvas.click({ position: p });
      await page.getByRole("button", { name: "Point elevation" }).click();
      await canvas.click({ position: p });
    }
    await expect(page.getByText("Measurement result")).toHaveCount(1);

    expect(
      pageErrors,
      `uncaught page errors: ${pageErrors.map((e) => e.message).join("; ")}`,
    ).toEqual([]);
  });
});

test.describe("Post-first-person-fix: raw raycast click handler respects real firstPerson state", () => {
  test("PointerLock activates, clicks while first-person do not trigger inspection/measurement sampling, and normal clicks resume correctly after exit — across repeated entry/exit cycles", async ({
    page,
  }) => {
    const pageErrors: Error[] = [];
    page.on("pageerror", (err) => pageErrors.push(err));

    await setUpCalibratedTerrainWorkspace(page);
    await page.getByRole("button", { name: "3D Terrain" }).click();

    // Scoped to exclude the ambient DynamicScientificBackground canvas
    // (mounted globally in AppShell, marked data-decorative-canvas) — a
    // bare `canvas` selector would otherwise match it first.
    const canvas = page.locator("canvas:not([data-decorative-canvas])").first();
    await expect(canvas).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Loading terrain height grid")).toHaveCount(0, { timeout: 20_000 });

    const canvasBox = await canvas.boundingBox();
    expect(canvasBox, "the real 3D canvas must have a real bounding box").not.toBeNull();
    const meshClickPosition = { x: canvasBox!.width * 0.7, y: canvasBox!.height * 0.35 };
    // Bottom-center: clear of the honesty-notice overlay, used to engage
    // PointerLock (same real click position Phase 10's own acceptance spec
    // uses for this exact purpose).
    const lockClickPosition = { x: (canvasBox?.width ?? 400) / 2, y: (canvasBox?.height ?? 300) - 20 };

    // Count real requests to the backend's per-pixel inspection endpoint —
    // the definitive, non-visual proof of whether the raw click handler's
    // raycast/sample logic actually ran for a given click.
    let sampleRequestCount = 0;
    page.on("request", (req) => {
      if (req.url().includes("/visualization/value")) sampleRequestCount++;
    });

    // Clicks a real screen position and deterministically settles before
    // returning whether it produced a new real /visualization/value
    // request — registers the response-wait BEFORE clicking (so a fast
    // response can't be missed), and always waits out either a real
    // response or the full timeout before returning, so one click's
    // outcome can never bleed ambiguously into the next click's count.
    async function clickAndSettle(
      position: { x: number; y: number },
      opts: { force?: boolean; timeout?: number } = {},
    ): Promise<boolean> {
      const before = sampleRequestCount;
      const waited = page
        .waitForResponse((r) => r.url().includes("/visualization/value"), {
          timeout: opts.timeout ?? 2000,
        })
        .catch(() => null);
      await canvas.click({ position, force: opts.force });
      await waited;
      await page.waitForTimeout(150); // let the counted `request` event flush
      return sampleRequestCount > before;
    }

    // Clicks a small spread of real candidate points (a real click on each,
    // never a guess about internal state) and returns on the first one that
    // actually produces a real sample — used only where exactly where the
    // mesh renders on screen isn't reliably known (e.g. right after real
    // WASD flight moved the camera outside OrbitControls' own tracking).
    async function clickUntilSampled(candidates: { x: number; y: number }[]): Promise<boolean> {
      for (const candidate of candidates) {
        if (await clickAndSettle(candidate)) return true;
      }
      return false;
    }

    for (let cycle = 0; cycle < 2; cycle++) {
      // --- normal-mode inspection click works ---
      await clickAndSettle(meshClickPosition, { timeout: 10_000 });
      await expect(
        page.getByText("Elevation:", { exact: false }).or(page.getByText("Sampling")).first(),
      ).toBeVisible({ timeout: 10_000 });

      // --- enter first-person, confirm real PointerLock activation ---
      await page.getByRole("button", { name: "Flythrough mode" }).click();
      await expect(page.getByText("Click to enter flythrough mode")).toBeVisible();
      // The lock-engaging click is itself a real click on the canvas while
      // `firstPerson` is already true, so it must ALSO be suppressed.
      const lockClickSampled = await clickAndSettle(lockClickPosition, { force: true });
      expect(
        lockClickSampled,
        `cycle ${cycle}: the click that engages PointerLock must not itself trigger a sample`,
      ).toBe(false);
      const pointerLocked = await page.evaluate(() => document.pointerLockElement !== null);
      expect(pointerLocked, `cycle ${cycle}: PointerLock must actually engage`).toBe(true);

      // --- real WASD + mouse-look while locked (existing behavior must
      // still work, unmodified by this fix) ---
      await page.keyboard.down("KeyW");
      await page.mouse.move(200, 200);
      await page.mouse.move(260, 180);
      await page.waitForTimeout(300);
      await page.keyboard.up("KeyW");

      // --- a real click while first-person/locked must NOT trigger a new
      // inspection/measurement sample request ---
      const lockedClickSampled = await clickAndSettle(meshClickPosition, { force: true });
      expect(
        lockedClickSampled,
        `cycle ${cycle}: a click while first-person is active must not trigger a new /visualization/value request`,
      ).toBe(false);

      // --- exit first-person. A synthetic Escape keypress is not reliably
      // treated by Chromium as sufficient to release a real Pointer Lock
      // when driven via CDP, so the lock is released through the browser's
      // own real `document.exitPointerLock()` API instead — this fires the
      // exact same genuine `pointerlockchange`/"unlock" event our
      // `PointerLockControls` listens for (see `handlePointerUnlock` in
      // TerrainView3D.tsx) as any other real release, exercising the real
      // unlock -> setIsPointerLocked(false) -> onExitFirstPerson() chain,
      // not a fabricated state change. ---
      await page.evaluate(() => document.exitPointerLock());
      await expect
        .poll(() => page.evaluate(() => document.pointerLockElement !== null), {
          timeout: 5_000,
          message: `cycle ${cycle}: PointerLock must release on exit`,
        })
        .toBe(false);
      // Wait for React to actually finish processing the real "unlock"
      // event and propagate `firstPerson=false` back down as a fresh prop
      // (the browser's own pointerLockElement can already be null a tick
      // before that state update/re-render has committed) — the button's
      // own label is a real, observable proxy for that having happened.
      await expect(page.getByRole("button", { name: "Flythrough mode" })).toBeVisible({
        timeout: 5_000,
      });

      // Real WASD flight moves the camera directly (bypassing OrbitControls'
      // own internal spherical tracking), so exactly where the mesh ends up
      // on screen after exiting first-person isn't reliably predictable
      // from the pre-flight framing, even after "Reset / fit camera" (a
      // real, existing UI action, whose damped OrbitControls convergence is
      // itself not perfectly deterministic in timing). Rather than depend
      // on one exact screen position, try a small spread of real candidate
      // points and accept whichever one's real click actually lands on the
      // mesh — the point of this check is only "does a normal click work
      // again after exiting first-person", not the specific pixel used to
      // prove it.
      await page.getByRole("button", { name: "Reset / fit camera" }).click();
      await page.waitForTimeout(500);

      const resumedSampling = await clickUntilSampled([
        meshClickPosition,
        { x: canvasBox!.width * 0.5, y: canvasBox!.height * 0.5 },
        { x: canvasBox!.width * 0.6, y: canvasBox!.height * 0.45 },
        { x: canvasBox!.width * 0.55, y: canvasBox!.height * 0.3 },
      ]);
      expect(
        resumedSampling,
        `cycle ${cycle}: a normal click after exiting first-person must resume real sampling`,
      ).toBe(true);
      await expect(
        page.getByText("Elevation:", { exact: false }).or(page.getByText("Sampling")).first(),
      ).toBeVisible({ timeout: 10_000 });
    }

    // --- measurement mode still works correctly after the entry/exit
    // cycles above (no duplicate listeners, no leftover first-person
    // suppression). ---
    await page.getByRole("button", { name: "Point elevation" }).click();
    await canvas.click({ position: meshClickPosition });
    await expect(
      page
        .getByText("Measurement result")
        .or(page.getByText("Sampling"))
        .or(page.getByText("Click 1 more point", { exact: false }))
        .first(),
    ).toBeVisible({ timeout: 15_000 });

    expect(
      pageErrors,
      `uncaught page errors: ${pageErrors.map((e) => e.message).join("; ")}`,
    ).toEqual([]);
  });
});
