/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, run the backend on :8000 (or via docker compose) and use `npm run dev`.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: false },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test/setup.ts"],
    // Deliberate: dates and numbers are formatted with the runtime's time zone and locale, so both are pinned for
    // the same output on every machine. The default forks pool starts its workers with this environment.
    env: { TZ: "UTC", LANG: "en_US.UTF-8", LC_ALL: "en_US.UTF-8" },
    testTimeout: 15_000,
    restoreMocks: true,
    unstubGlobals: true,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/main.tsx", "src/vite-env.d.ts"],
      reporter: ["text-summary", "text", "html", "json-summary"],
      reportsDirectory: "coverage",
      // Just under the current numbers: `npm run test:coverage` fails when coverage drops.
      thresholds: { statements: 92, branches: 87, functions: 91, lines: 94 },
    },
  },
});
