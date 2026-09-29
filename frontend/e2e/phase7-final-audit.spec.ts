import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const FIXTURES = path.resolve(import.meta.dirname, "fixtures");
const WIDTHS = [390, 412, 768, 1024, 1280, 1440, 1920] as const;

function unique(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
}

async function register(page: Page, prefix: string) {
  await page.goto("/register");
  await page.getByRole("textbox", { name: "Email" }).fill(`${unique(prefix)}@example.com`);
  await page.getByLabel("Password").fill("Phase7FinalPass123!");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/dashboard/);
}

async function createProject(page: Page, name: string) {
  await page.getByRole("link", { name: "Projects" }).first().click();
  await page.getByRole("textbox", { name: "Name" }).fill(name);
  await page.getByRole("textbox", { name: "Description" }).fill("Final UI validation");
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("link", { name }).click();
}

async function upload(page: Page, filename: string, role?: "DEM reference") {
  if (role) await page.getByRole("button", { name: role }).click();
  const before = await page.getByText("valid", { exact: false }).count();
  await page.locator('input[type="file"]').first().setInputFiles(path.join(FIXTURES, filename));
  await expect(page.getByText("valid", { exact: false })).toHaveCount(before + 1, { timeout: 20_000 });
}

async function expectNoDocumentOverflow(page: Page, width: number, height = 900) {
  await page.setViewportSize({ width, height });
  const dimensions = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scroll, `${width}px document width`).toBeLessThanOrEqual(dimensions.client);
}

async function expectBasicSemantics(page: Page) {
  const issues = await page.evaluate(() => {
    const visible = (element: Element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const name = (element: Element) => {
      const id = element.getAttribute("id");
      const explicit = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
      return element.getAttribute("aria-label") || element.getAttribute("aria-labelledby") ||
        explicit?.textContent || element.closest("label")?.textContent || element.textContent ||
        element.getAttribute("title") || "";
    };
    const unnamed = [...document.querySelectorAll("button, a[href], input, select, textarea")]
      .filter(visible).filter((element) => !name(element).trim())
      .map((element) => element.outerHTML.slice(0, 160));
    const ids = [...document.querySelectorAll("[id]")].map((element) => element.id);
    const duplicateIds = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
    const imagesWithoutAlt = [...document.querySelectorAll("img")].filter(visible)
      .filter((image) => !image.hasAttribute("alt")).map((image) => image.outerHTML.slice(0, 160));
    return { unnamed, duplicateIds, imagesWithoutAlt, hasMain: Boolean(document.querySelector("main")) };
  });
  expect(issues).toEqual({ unnamed: [], duplicateIds: [], imagesWithoutAlt: [], hasMain: true });
}

test.describe("Phase 7 final responsive, accessibility, and performance audit", () => {
  test("auth, dashboard, and projects are keyboard/semantic/responsive and dashboard requests deduplicate", async ({ page }) => {
    await page.goto("/register");
    await page.waitForLoadState("networkidle");
    const coldScripts = await page.evaluate(() => performance.getEntriesByType("resource")
      .map((entry) => entry as PerformanceResourceTiming)
      .filter((entry) => /\.(js|tsx?|jsx)(\?|$)/.test(entry.name))
      .reduce((summary, entry) => ({
        requests: summary.requests + 1,
        transferBytes: summary.transferBytes + entry.transferSize,
        decodedBytes: summary.decodedBytes + entry.decodedBodySize,
      }), { requests: 0, transferBytes: 0, decodedBytes: 0 }));
    test.info().annotations.push({ type: "cold-route", description: JSON.stringify(coldScripts) });
    await expectBasicSemantics(page);
    await page.keyboard.press("Tab");
    await expect(page.getByRole("textbox", { name: "Email" })).toBeFocused();
    for (const width of WIDTHS) await expectNoDocumentOverflow(page, width, width < 768 ? 844 : 900);

    await register(page, "phase7-shell");
    await expectBasicSemantics(page);
    await page.getByRole("link", { name: "Projects" }).first().click();
    const names = [`Phase 7 Network A ${Date.now()}`, `Phase 7 Network B ${Date.now()}`];
    for (const name of names) {
      await page.getByRole("textbox", { name: "Name" }).fill(name);
      await page.getByRole("button", { name: "Create project" }).click();
      await expect(page.getByRole("link", { name })).toBeVisible();
    }

    const requests: string[] = [];
    page.on("request", (request) => {
      const pathname = new URL(request.url()).pathname;
      if (pathname.startsWith("/api/v1/")) requests.push(pathname);
    });
    const started = Date.now();
    await page.goto("/dashboard");
    await expect(page.getByText("2").first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    const projectLists = requests.filter((value) => value === "/api/v1/projects");
    const datasetLists = requests.filter((value) => value.endsWith("/datasets"));
    const analysisLists = requests.filter((value) => value.endsWith("/analysis"));
    expect({ projectLists: projectLists.length, datasetLists: datasetLists.length, analysisLists: analysisLists.length })
      .toEqual({ projectLists: 1, datasetLists: 2, analysisLists: 2 });
    test.info().annotations.push({ type: "dashboard", description: JSON.stringify({ durationMs: Date.now() - started, requestCount: requests.length }) });
    await expectBasicSemantics(page);
    for (const width of WIDTHS) await expectNoDocumentOverflow(page, width, width < 768 ? 844 : 900);
  });

  test("real final workflow preserves scientific truth, engine state, accessibility, and responsive layouts", async ({ page }) => {
    test.setTimeout(180_000);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 1440, height: 900 });
    await register(page, "phase7-flow");
    const projectName = `Phase 7 Final ${Date.now()}`;
    await createProject(page, projectName);
    await upload(page, "calibrated-source.tif");
    await upload(page, "calibrated-dem.tif", "DEM reference");

    const analysisStarted = Date.now();
    await page.getByRole("button", { name: "Analysis" }).click();
    await expect(page.getByRole("heading", { name: "Available analysis" })).toBeVisible();
    test.info().annotations.push({ type: "analysis", description: `${Date.now() - analysisStarted}ms` });
    await page.getByRole("combobox", { name: "Dataset" }).selectOption({ index: 1 });
    await page.getByRole("button", { name: "DEM reference" }).click();
    await page.getByRole("combobox", { name: "DEM reference dataset" }).selectOption({ index: 1 });
    await page.getByRole("button", { name: "Start Analysis" }).click();
    await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("Relative Depth", { exact: true }).last()).toBeVisible();
    await expect(page.getByText("Metric Elevation", { exact: true }).last()).toBeVisible();
    await expect(page.getByText("Unknown", { exact: true })).toHaveCount(0);
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: "e2e/screenshots/phase7-final-analysis-1440.png", fullPage: true });

    const previewRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/visualization\/(map-preview|preview)$/.test(new URL(request.url()).pathname)) previewRequests.push(request.url());
    });
    const workspaceStarted = Date.now();
    await page.getByRole("article", { name: /Analysis job/ }).first().getByRole("button", { name: "Open in workspace" }).click();
    await expect(page.locator(".leaflet-container")).toBeVisible({ timeout: 20_000 });
    test.info().annotations.push({ type: "workspace-2d", description: `${Date.now() - workspaceStarted}ms` });
    const engine = page.getByTestId("terrain-engine-host");
    await engine.evaluate((element) => element.setAttribute("data-phase7-persistent", "true"));
    await page.waitForLoadState("networkidle");
    const threeStarted = Date.now();
    await page.getByRole("button", { name: "3D Terrain" }).click();
    await expect(page.locator("canvas:not([data-decorative-canvas])")).toBeVisible({ timeout: 30_000 });
    test.info().annotations.push({ type: "workspace-3d", description: `${Date.now() - threeStarted}ms` });
    await page.getByRole("button", { name: "2D Map" }).click();
    await expect(page.locator(".leaflet-container")).toBeVisible();
    await page.waitForLoadState("networkidle");
    const settledPreviewCount = previewRequests.length;

    await page.getByRole("button", { name: "Measurements" }).click();
    await expect(page.getByRole("button", { name: "Point elevation" })).toBeVisible();
    await page.getByRole("button", { name: "Disaster" }).click();
    await expect(page.getByText("Screening, not prediction", { exact: false }).first()).toBeVisible();
    await page.getByRole("button", { name: "Terrain", exact: true }).click();
    await page.getByRole("button", { name: "Flythrough", exact: true }).click();
    await expect(page.getByTestId("flythrough-path-card")).toBeVisible();
    await expect(page.getByRole("button", { name: "Export 3D mesh (GLB)" })).toBeVisible();

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: "e2e/screenshots/phase7-final-workspace-1440.png", fullPage: true });
    await expectBasicSemantics(page);
    for (const width of WIDTHS) await expectNoDocumentOverflow(page, width, width < 768 ? 844 : 900);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: "e2e/screenshots/phase7-final-workspace-390.png", fullPage: true });

    await page.getByRole("button", { name: "Analysis" }).click();
    await expect(engine).toHaveAttribute("data-phase7-persistent", "true");
    expect(previewRequests).toHaveLength(settledPreviewCount);
    await expect(page.getByText("completed", { exact: true }).first()).toBeVisible();
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: "e2e/screenshots/phase7-final-analysis-390.png", fullPage: true });
    await expectBasicSemantics(page);

    const reportsStarted = Date.now();
    await page.getByRole("button", { name: "Reports" }).click();
    await page.getByRole("combobox", { name: "Report source dataset" }).selectOption({ index: 1 });
    await page.getByRole("button", { name: "Generate report" }).click();
    const reportCard = page.getByRole("article", { name: "Terrain analysis report" }).first();
    await expect(reportCard.locator("header").getByText("Completed", { exact: true })).toBeVisible({ timeout: 30_000 });
    test.info().annotations.push({ type: "reports", description: `${Date.now() - reportsStarted}ms` });
    await expect(engine).toHaveAttribute("data-phase7-persistent", "true");
    expect(previewRequests).toHaveLength(settledPreviewCount);
    await expectBasicSemantics(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: "e2e/screenshots/phase7-final-reports-390.png", fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: "e2e/screenshots/phase7-final-reports-1440.png", fullPage: true });
    for (const width of WIDTHS) await expectNoDocumentOverflow(page, width, width < 768 ? 844 : 900);
  });
});
