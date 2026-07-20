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
  "@/lib/api",
  "@/lib/api/*",
  "@/lib/db",
  "@/lib/db/*",
  "@/lib/auth/server",
  "@/lib/auth/server/*",
  "server-only",
];

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
