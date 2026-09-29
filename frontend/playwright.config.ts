import { defineConfig } from "@playwright/test";

// Phase 10 final browser acceptance only — a minimal, targeted config
// against the ALREADY RUNNING real application (docker compose: frontend
// on :5173, backend on :8000). This does not start/manage either service
// itself; both must already be up. Not a general-purpose E2E framework
// rollout — see e2e/phase10-terrain.spec.ts for the one acceptance test
// this exists to run.
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  retries: 0,
  workers: 1,
  use: {
    baseURL: "http://localhost:5173",
    screenshot: "on",
    video: "off",
    trace: "off",
  },
});
