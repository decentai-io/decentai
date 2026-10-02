// Container / self-hosted build. The SPA is served by Caddy, which reverse-
// proxies the ONE backend (gateway, uploads, downloads, chat WebSocket) on
// the SAME origin, so the API base is just '/' and one image runs on any
// host without a rebuild.
export const environment = {
  production: true,
  apiEndpoint: '/',
  domain: '',
};
