# syntax=docker/dockerfile:1.7
# Nimbrix web image (Next.js standalone output). Build context: repository root.

FROM node:24-alpine AS deps
WORKDIR /repo
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/package.json packages/contracts/package.json
RUN --mount=type=cache,target=/root/.npm \
    npm ci --workspace @nimbrix/web --include-workspace-root=false --ignore-scripts --no-audit --no-fund

FROM node:24-alpine AS builder
WORKDIR /repo
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /repo/node_modules ./node_modules
COPY package.json package-lock.json ./
COPY packages/contracts ./packages/contracts
COPY apps/web ./apps/web
RUN npm run build --workspace @nimbrix/web

FROM node:24-alpine AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
RUN addgroup -S -g 10001 nimbrix && adduser -S -u 10001 -G nimbrix nimbrix
WORKDIR /app
COPY --from=builder --chown=nimbrix:nimbrix /repo/apps/web/.next/standalone ./
COPY --from=builder --chown=nimbrix:nimbrix /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=nimbrix:nimbrix /repo/apps/web/public ./apps/web/public
USER nimbrix:nimbrix
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "apps/web/server.js"]
