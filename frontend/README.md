# DecentAI frontend

The web app of DecentAI: an Angular single-page application. It talks to
one backend over the same origin — sign-in, the app gateway, file storage
and the chat WebSocket.

## Develop

```
npm install
npm start
```

`npm start` serves the app at `http://localhost:4200`. It expects the
backend at `http://localhost:8000` (see `src/environments/environment.ts`),
so start the backend first; the repository's main README says how.

## Test

```
npm test
```

Runs the unit tests in Chrome through Karma.

## Build

```
npm run build
```

writes a production build to `dist/decentai`.

The container image is built from the repository root, not from this
folder:

```
docker build -t decentai-caddy:local -f frontend/Dockerfile .
```

It builds the app and serves it with Caddy, which also proxies the
backend's paths on the same address (`Caddyfile`).
