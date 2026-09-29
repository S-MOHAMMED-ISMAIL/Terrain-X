import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    watch: {
      usePolling: true,
    },
  },
  test: {
    // Phase 10: `e2e/` holds real Playwright browser acceptance specs
    // (run via `npx playwright test`, its own separate tool/config) — must
    // be excluded here or Vitest's default `**/*.spec.ts` glob picks them
    // up too and fails, since they call Playwright's own `test.describe`.
    exclude: ["**/node_modules/**", "**/e2e/**"],
  },
});
