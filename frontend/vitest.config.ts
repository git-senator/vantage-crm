import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Unit tests for the frontend.
 *
 * Scope is deliberate: pure logic and component behaviour, not pages. Server
 * components do network IO through `server-only` modules and are covered by the
 * API-level end-to-end checks instead — mounting them here would mean mocking
 * the whole data layer to assert on markup, which tests the mock.
 *
 * `environment: "jsdom"` only applies to files that need a DOM; the query and
 * formatter tests run just as well without it, but a single environment keeps
 * the config honest.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // The Next build output and node_modules contain their own test files.
    exclude: ["node_modules/**", ".next/**"],
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
