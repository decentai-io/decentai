# DecentAI for the desktop

DecentAI on your own computer, for Windows and macOS. Free.

**[Download the latest release](https://github.com/decentai-io/decentai-desktop/releases/latest)**

- **Windows:** `DecentAI-Setup.exe`
- **macOS:** `DecentAI.dmg`, for Intel and Apple silicon

DecentAI runs on your computer, in containers (Docker Desktop or Podman), with your data staying there.
The app can make it reachable from your phone or a browser anywhere: through Tailscale, ngrok, Cloudflare
Tunnel, or a [DecentAI relay](https://github.com/decentai-io/decentai-relay) you run or trust. Each way has a
step-by-step guide inside the app.

The app updates itself, and DecentAI on your computer follows it.

## What a release holds

| File | |
|---|---|
| `DecentAI-Setup.exe`, `DecentAI.dmg` | the installers |
| `DecentAI.app.tar.gz`, `*.sig`, `latest.json` | what installed apps update themselves from, signed |
| `release.json`, `release.json.sig` | every image DecentAI runs, named by its digest, signed |
| `decentai-engine-deploy-*.tar.gz` | the engine's deploy files that `release.json` names |

The images are public packages of this organization (`ghcr.io/decentai-io/desktop-*`), compiled.

## Licence

This repository holds releases only. DecentAI is proprietary software of DECENT INTELLIGENCE ARTIFICIAL
SERVICES, free to use on your own computer with the licence the app carries (personal: any number of
people, one runtime and one sandbox, no end date). Running DecentAI on servers for an organization is a
separate licence: [decentai.io](https://decentai.io). Questions and problems: support@decentai.io.
