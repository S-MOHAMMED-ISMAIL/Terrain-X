import { expect, test } from "@playwright/test";

function uniqueEmail(): string {
  return `phase2-auth-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

test.describe("Phase 2 authentication accessibility foundation", () => {
  test("registration, logout, login, protected redirect, labels, errors, and busy state remain accessible", async ({
    page,
  }) => {
    const email = uniqueEmail();
    const password = "Phase2AccessiblePass123!";

    await page.goto("/register");
    const registerEmail = page.getByLabel(/^Email/);
    const registerPassword = page.getByLabel(/^Password/);
    await expect(registerEmail).toHaveAttribute("autocomplete", "email");
    await expect(registerPassword).toHaveAttribute("autocomplete", "new-password");
    await registerEmail.fill(email);
    await registerPassword.fill(password);
    await page.getByRole("button", { name: "Create account" }).click();
    await page.waitForURL(/\/dashboard/);

    await page.getByRole("button", { name: "Logout" }).click();
    await page.waitForURL(/\/login/);
    const loginEmail = page.getByLabel(/^Email/);
    const loginPassword = page.getByLabel(/^Password/);
    await expect(loginEmail).toHaveAttribute("autocomplete", "email");
    await expect(loginPassword).toHaveAttribute("autocomplete", "current-password");

    await page.route("**/api/v1/auth/login", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 350));
      await route.continue();
    }, { times: 1 });
    await loginEmail.fill(email);
    await loginPassword.fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    const busyButton = page.getByRole("button", { name: "Signing in…" });
    await expect(busyButton).toHaveAttribute("aria-busy", "true");
    await expect(busyButton).toBeDisabled();
    await page.waitForURL(/\/dashboard/);

    await page.getByRole("button", { name: "Logout" }).click();
    await loginEmail.fill(email);
    await loginPassword.fill("IncorrectPassword123!");
    await page.getByRole("button", { name: "Sign in" }).click();
    const alert = page.getByRole("alert");
    await expect(alert).toBeVisible();
    await expect(loginEmail).toHaveAttribute("aria-describedby", /login-error/);
    await expect(loginPassword).toHaveAttribute("aria-describedby", /login-error/);
    await expect(alert).not.toContainText(password);

    await page.evaluate(() => localStorage.removeItem("terrainx_access_token"));
    await page.goto("/projects");
    await page.waitForURL(/\/login/);
  });

  test("authentication primitives do not overflow baseline viewports and respect reduced motion", async ({
    page,
  }) => {
    for (const viewport of [
      { width: 390, height: 844 },
      { width: 768, height: 900 },
      { width: 1024, height: 900 },
      { width: 1440, height: 1000 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/login");
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
      );
      expect(overflow, `login must not overflow at ${viewport.width}px`).toBe(false);
    }

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/login");
    const durationMs = await page.locator("form").evaluate((form) => {
      const duration = getComputedStyle(form).animationDuration;
      return duration.endsWith("ms") ? Number.parseFloat(duration) : Number.parseFloat(duration) * 1000;
    });
    expect(durationMs).toBeLessThanOrEqual(0.001);
  });
});
