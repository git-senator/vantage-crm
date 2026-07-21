import "server-only";

import { API_PREFIX_PATH } from "@/lib/api/paths";

/**
 * Server-only API configuration.
 *
 * `API_INTERNAL_URL` describes internal network topology and must never reach
 * a browser bundle. The `server-only` import makes a client import a build
 * failure rather than a leak.
 *
 * Client-safe constants (cookie names, CSRF header) live in `constants.ts`.
 */
export const API_INTERNAL_URL =
  process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000";

export const API_PREFIX = API_PREFIX_PATH;
