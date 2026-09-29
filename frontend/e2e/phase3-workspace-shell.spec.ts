import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const FIXTURES = path.resolve(import.meta.dirname, "fixtures");

function uniqueEmail(): string {
  return `phase3-shell-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

async function openRelativeWorkspace(page: Page): Promise<void> {
  await page.goto("/register");
  await page.locator('input[type="email"]').fill(uniqueEmail());
  await page.locator('input[type="password"]').fill("Phase3WorkspacePass123!");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/dashboard/);

  await page.getByRole("link", { name: "Projects" }).first().click();
  const projectName = `Phase 3 Workspace ${Date.now()}`;
  await page.getByPlaceholder("e.g. Coastal Flood Study").fill(projectName);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("link", { name: projectName }).click();

  await page.locator('input[type="file"]').first().setInputFiles(
    path.join(FIXTURES, "uncalibrated-source.jpg"),
  );
  await expect(page.getByText("valid", { exact: false }).first()).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.locator("select").first().selectOption({ index: 1 });
  await page.getByRole("button", { name: "Start Analysis" }).click();
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: "Terrain", exact: true }).click();
  await page.getByLabel("Dataset").selectOption({ index: 1 });
  await expect(page.getByTestId("terrain-workspace-shell")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".leaflet-container")).toBeVisible({ timeout: 15_000 });
}

async function assertNoHorizontalOverflow(page: Page, label: string): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(overflow, `${label} must not introduce horizontal page overflow`).toBe(false);
}

test.describe("Phase 3 terrain workspace shell", () => {
  test("preserves the viewport, supports panels/modes/layers, and captures responsive baselines", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openRelativeWorkspace(page);

    const shell = page.getByTestId("terrain-workspace-shell");
    const host = page.getByTestId("terrain-engine-host");
    await host.evaluate((element) => element.setAttribute("data-persistence-check", "mounted"));

    const quality = shell.getByRole("region", { name: "Data quality" });
    const qualitySummary = quality.locator("dl").first();
    await expect(qualitySummary.getByText("Relative", { exact: true })).toBeVisible();
    await expect(qualitySummary.getByText("Unitless", { exact: true })).toBeVisible();
    await expect(qualitySummary.getByText("Not available", { exact: true })).toBeVisible();

    const relativeRow = page
      .getByRole("button", { name: "Relative Depth (Uncalibrated)", exact: true })
      .locator("xpath=..");
    await page.getByRole("button", { name: "Relative Depth (Uncalibrated)", exact: true }).click();
    await relativeRow.getByRole("checkbox", { name: "Visible" }).click();
    await expect(page.getByRole("button", { name: "Relative Depth (Uncalibrated)", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await page.getByRole("button", { name: "Hide layers" }).click();
    await page.getByRole("button", { name: "Hide tools" }).click();
    await page.getByRole("button", { name: "Hide results" }).click();
    await expect(host).toHaveAttribute("data-persistence-check", "mounted");
    await expect(page.locator(".leaflet-container")).toBeVisible();
    await page.getByRole("button", { name: "Show layers" }).click();
    await page.getByRole("button", { name: "Show tools" }).click();
    await page.getByRole("button", { name: "Show results" }).click();

    const primaryText = await shell.getByRole("heading", { name: /Phase 3 Workspace/ }).evaluate(
      (element) => getComputedStyle(element).color,
    );
    expect(primaryText).toBe("rgb(242, 245, 247)");
    await assertNoHorizontalOverflow(page, "1440px Explore");
    await page.waitForTimeout(300);
    await page.screenshot({ path: "e2e/screenshots/phase3-1440-explore.png", fullPage: true });

    await page.setViewportSize({ width: 1024, height: 768 });
    await shell.getByRole("button", { name: "Measure", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Measure tools" })).toBeVisible();
    await assertNoHorizontalOverflow(page, "1024px Measure");
    await page.waitForTimeout(300);
    await page.screenshot({ path: "e2e/screenshots/phase3-1024-measure.png", fullPage: true });

    await page.setViewportSize({ width: 768, height: 900 });
    await shell.getByRole("button", { name: "Screen", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Screen tools" })).toBeVisible();
    await expect(page.getByText("Screening, not prediction")).toBeVisible();
    await assertNoHorizontalOverflow(page, "768px Screen");
    await page.waitForTimeout(300);
    await page.screenshot({ path: "e2e/screenshots/phase3-768-screen.png", fullPage: true });

    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Flythrough", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Flythrough tools" })).toBeVisible();
    await assertNoHorizontalOverflow(page, "390px Flythrough");
    await page.waitForTimeout(300);
    await page.screenshot({ path: "e2e/screenshots/phase3-390-flythrough.png", fullPage: true });

    await page.getByRole("button", { name: "3D Terrain" }).click();
    await expect(page.locator("canvas:not([data-decorative-canvas])")).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: "2D Map" }).click();
    await expect(page.locator(".leaflet-container")).toBeVisible({ timeout: 15_000 });
  });
});
