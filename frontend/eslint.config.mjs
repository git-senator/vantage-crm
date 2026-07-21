import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

import boundaryPlugin from "./eslint-rules/no-server-data-in-client.mjs";

/**
 * Modules that must never cross into a client component.
 *
 * Phase 1 adds the API and session layers here as they land; the `*` suffixes
 * are already in place so new files under those paths are covered on creation
 * rather than when someone remembers to update this list.
 */
const SERVER_ONLY_MODULES = [
  "@/lib/mock-data",
  // Holds the internal API hostname.
  "@/lib/api/config",
  // Reads httpOnly cookies via next/headers.
  "@/lib/api/server",
  // Session resolution — issues authenticated calls on the user's behalf.
  "@/lib/auth/session",
  "@/lib/db",
  "@/lib/db/*",
  "server-only",
];

/*
 * Deliberately NOT restricted:
 *   @/lib/api/types     — type-only, erased at compile time
 *   @/lib/api/constants — cookie names and the CSRF header, which the browser
 *                         must read to implement double-submit CSRF
 *   @/lib/api/paths     — the public /api/v1 prefix
 *
 * `import "server-only"` in each restricted module is the backstop: anything
 * this list misses still fails the build rather than shipping to a browser.
 */

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,

  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { boundary: boundaryPlugin },
    rules: {
      "boundary/no-server-data-in-client": [
        "error",
        { restricted: SERVER_ONLY_MODULES },
      ],
    },
  },

  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
