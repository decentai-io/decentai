# ---------------------------------------------------------------------------
# Stage 1: build the Angular SPA (container configuration → apiEndpoint '/',
# origin-agnostic: one image runs on any host without a rebuild).
# Build context is the REPOSITORY ROOT: docker build -f frontend/Dockerfile .
# ---------------------------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npx ng build --configuration container

# ---------------------------------------------------------------------------
# Stage 2: Caddy is the deployment's single front door — it terminates TLS
# (automatic certificates), serves the SPA, and reverse-proxies the backend
# paths on the same origin, including the chat WebSocket.
# ---------------------------------------------------------------------------
FROM caddy:2-alpine

COPY frontend/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist/decentai/browser /srv

# Caddy's admin API answers on localhost:2019 whenever the process is up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:2019/config/ || exit 1
