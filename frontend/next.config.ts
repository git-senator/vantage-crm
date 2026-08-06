import type { NextConfig } from "next";

/**
 * Browser-facing security headers.
 *
 * These live here rather than in FastAPI because Next.js is the only service
 * the browser talks to — the API sits on the internal network behind it
 * (docs/ARCHITECTURE.md §2).
 *
 * `style-src 'unsafe-inline'` is a documented, deliberate exception: Tailwind
 * emits inline styles. Scripts do NOT get that exemption. Phase 1 introduces a
 * per-request nonce for script-src once the auth flow adds inline bootstrap.
 */
/**
 * Uploads and downloads talk directly to object storage on its own host
 * (`S3_PUBLIC_ENDPOINT_URL`, e.g. https://files.<domain>). The browser PUTs
 * there and renders image previews from there, so that origin must be allowed
 * in `connect-src` (the upload fetch) and `img-src` (the preview). Read at build
 * time and passed in as a build arg; empty in envs without object storage.
 */
const s3Origin = (() => {
  const raw = process.env.S3_PUBLIC_ENDPOINT_URL;
  if (!raw) return "";
  try {
    return new URL(raw).origin;
  } catch {
    return "";
  }
})();
const withS3 = (directive: string) => (s3Origin ? `${directive} ${s3Origin}` : directive);

const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      withS3("img-src 'self' data: blob:"),
      "font-src 'self' data:",
      "style-src 'self' 'unsafe-inline'",
      // 'unsafe-eval' is required by the Next.js dev overlay only.
      process.env.NODE_ENV === "development"
        ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
        : "script-src 'self' 'unsafe-inline'",
      withS3("connect-src 'self'"),
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "object-src 'none'",
    ].join("; "),
  },
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "geolocation=(), camera=(), microphone=(), interest-cohort=()",
  },
];

const nextConfig: NextConfig = {
  // Traced standalone bundle for a minimal runtime container.
  output: "standalone",

  // Do not advertise the framework version to scanners.
  poweredByHeader: false,

  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
