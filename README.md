# DecentAI

DecentAI is an agentic AI platform you run yourself: on your own computer, on a server, in your data centre or in
your cloud. This repository is where it is released. It holds releases and documentation, and no source.

**[The latest release](https://github.com/decentai-io/decentai/releases/latest)**

## One release, every way of running it

Every release is the same two things, whatever runs it:

- **The images**, public and compiled, at `ghcr.io/decentai-io`: the engine's (`decentai-api-server`,
  `decentai-ai-runtime`, `decentai-issuer`, `decentai-sandbox`), the web client's (`decentai-web-client`), and the
  third-party ones the engine runs, copied in with their digests unchanged (`mongo`, `caddy`). Everything is pulled
  from this one registry. The engine's images are signed with [cosign](https://docs.sigstore.dev/) by the release
  workflow that built them.
- **The release files**, attached to each release here:

| File | |
|---|---|
| `decentai-X.Y.Z.tar.gz` | the deploy bundle: the Compose files, the settings' template, `setup.py` and the sandbox's profiles |
| `images.json` | every image the release runs, by digest |
| `SHA256SUMS` | the checksums of the two above |

## Running it

- **On a server**, a private data centre's or a cloud's: download the bundle of the latest release, then
  `python3 deploy/setup/setup.py --domain ai.example.com` (the release notes give the exact commands).
- **On your own computer**: the DecentAI desktop app, for Windows and macOS, is attached to releases once it is out.
- **From your phone**: the DecentAI app, on the App Store and Google Play, given your deployment's address.

## Licence

DecentAI is proprietary software of DECENT INTELLIGENCE ARTIFICIAL SERVICES. Without a licence a deployment runs in
evaluation (3 people, 1 runtime, 1 sandbox); a licence file sets what it may do. For a licence, or any question:
support@decentai.io, [decentai.io](https://decentai.io).
