# syntax=docker/dockerfile:1.7
FROM node:20-alpine AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund

FROM node:20-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY frontend ./
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
RUN addgroup -g 10002 -S tradeloom && adduser -u 10002 -S tradeloom -G tradeloom
COPY --from=builder --chown=tradeloom:tradeloom /app/.next/standalone ./
COPY --from=builder --chown=tradeloom:tradeloom /app/.next/static ./.next/static
COPY --from=builder --chown=tradeloom:tradeloom /app/public ./public
USER tradeloom
EXPOSE 3000
CMD ["node", "server.js"]
