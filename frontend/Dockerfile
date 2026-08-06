# Multi-stage Next.js build using standalone output.
#
# `output: "standalone"` traces the exact runtime dependency set, so the final
# image carries neither node_modules nor the build toolchain.

# --------------------------------------------------------------------- deps
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# -------------------------------------------------------------------- build
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# The object-storage origin is baked into the CSP at build time (next.config.ts
# reads it here), so the browser may PUT uploads and load previews from it.
ARG S3_PUBLIC_ENDPOINT_URL=""
ENV S3_PUBLIC_ENDPOINT_URL=$S3_PUBLIC_ENDPOINT_URL
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ------------------------------------------------------------------ runtime
FROM node:22-alpine AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN apk add --no-cache curl \
    && addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:3000/login || exit 1

CMD ["node", "server.js"]
