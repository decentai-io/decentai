# Known gaps

What the redesigned platform still has to fix, test or decide, found on 2026-11-10 by comparing it with the platform
before the redesign (`decentai-io/decentai` at `007e4da`, 2026-10-26, kept in the offline archive
`decentai_archive_old.zip`; "the old" below). Each item has an ID to refer to it by in commits and issues; tick it
when it is done, or strike it with the reason when it is dropped on purpose.

Items marked **(checked)** were confirmed in the code when this list was written; the others come from a review of
the code and are confirmed first when their turn comes. Places are as of the commit that added this file.

## Security

- [ ] **S1 (checked)** MCP tool calls pass no gate: no approval card, no trust level, no check of their inputs. A turn
  begun by a webhook or a schedule, with the outside text in the prompt, can make side-effecting MCP calls.
  `runtime/src/mind/brain.py` (`use`) → `runtime/src/chats/chat.py` (`mcp`). *Do:* send MCP calls through the gate
  as agents' calls are; the old did (`ai_runtime/agents/mcp.py`).
- [ ] **S2 (checked)** An approval is reusable. `Gate._approved` takes any answered card in the chat whose call id
  matches the one the caller gives; the card is not bound to the agent, function, resource or operation, and is
  never spent. `api_server/src/records/agents.py`. *Do:* bind the card to what it approved and spend it once.
- [ ] **S3 (checked)** An enrollment token an administrator makes has unlimited uses unless they set `uses`, for 7
  days, and its certificates may live up to the intermediate's 120 days. `issuer/controllers.py` (`create`),
  `api_server/src/runtimes/endpoints.py`. *Do:* default to one use and a short life.
- [ ] **S4** The issuer does not ask whether a node was refused: a refused runtime keeps making sandbox tokens and
  enrolling sandboxes until its certificate expires (12 h). Only renewal checks. `issuer/controllers.py`
  (`Rules.allowed`, `TokenController.create`). *Do:* check refusal on every token and enrollment.
- [ ] **S5 (checked)** The way out forwards a plain-HTTP request without rewriting its `Host` header, so a declared
  host on a shared address (a CDN) reaches other sites on it. Its copy loop has no idle timeout, and no connection is
  recorded. `runtime/src/way_out/proxy.py`. *Do:* set `Host` from the checked target, add an idle timeout, record
  each connection; the old did all three (`ai_runtime/agents/egress.py`).
- [ ] **S6** `FileDoor.piece` serves any offered file, and any package by digest, to any sandbox that asks; it is not
  bound to the sandbox it was offered to. `runtime/src/sandboxes/sandboxes.py`. *Do:* a ticket per offer, as the old
  had (`ai_runtime/agents/sandboxes.py`).
- [ ] **S7 (checked)** The sandbox container's seccomp profile allows by default (`SCMP_ACT_ALLOW`) and denies about
  30 calls; the old kept Docker's default allowlist. `sandbox/config/seccomp.json`. *Do:* start from Docker's
  default and add only what nsjail needs.
- [ ] **S8 (checked)** The sandbox runs with `systempaths=unconfined`, and each jail mounts a fresh `/proc`, so
  agents' code reads the host's world-readable `/proc` that Docker would mask (`/proc/timer_list` and others).
  `deploy/docker-compose.yml`, `sandbox/config/jail.cfg`. *Do:* mask those paths inside the jail.
- [ ] **S9** The compose file sets no AppArmor profile, though [sandbox](sandbox.md) §9 requires
  `apparmor=decentai-sandbox`; on Ubuntu the jail's probe then fails and an operator is tempted to `unconfined`. The
  profile given is itself broad (`capability, file, mount, userns`). *Do:* set it in the compose file and narrow it.
- [ ] **S10** The jail's own seccomp list misses calls the old blocked: `open_tree`, `move_mount`, `fsopen`,
  `fsmount`, `fspick`, `mount_setattr`, `pidfd_getfd`. `sandbox/config/jail.cfg`. *Do:* add them (defence in depth;
  no capabilities and the user-namespace block cover them today).
- [ ] **S11** The jail sees the container's `/etc`; nothing caps the disk `/data/programs` uses; the sandbox has no
  healthcheck. `sandbox/config/jail.cfg`, `sandbox/Dockerfile`.
- [ ] **S12 (checked)** `deploy/config/deploy.env.example` writes `change-me` for the database passwords
  (`MONGO_ROOT_PASSWORD`, `AI_API_DATABASE_URI`, `ISSUER_DATABASE_PASSWORD`), and nothing refuses them; only the
  encryption key is checked. `setup.py` replaces them, a hand-made file may not. *Do:* refuse `change-me` at start.
- [ ] **S13** The outside-call check (SSRF) does not treat NAT64 addresses (`64:ff9b::/96`) as private; the old did.
  `api_server/src/utils.py`.
- [ ] **S14** The issuer writes its intermediate's private key unencrypted (mode 0600), and the first run's setup code
  is written to the log. `issuer/authority.py`, `api_server/src/__main__.py`.
- [ ] **S15** Rate limits are by client address: with `AI_API_PROXIES` wrong, everyone behind one proxy shares one
  sign-in bucket (20 tries in 15 minutes). `api_server/src/doors/limits.py`. *Do:* say so in
  [operating](operating.md), and warn at start when requests come only from a proxy that is not listed.
- [ ] **S16** A picture's type is taken from its file name, not its first bytes; the old checked the bytes.
  `runtime/src/agents/functions.py`.
- [ ] **S17** The `fake` model provider can be chosen in a chat's settings in production.
  `runtime/src/models/factory.py`. *Do:* allow it in tests only.
- [ ] **S18** Checking a model connection echoes up to 500 characters of the endpoint's error, and model endpoints are
  not held to public addresses (the old was the same). `runtime/src/api/endpoints.py`.
- [ ] **S19** The speech service asks no one who is calling, on the stack's default network. `speech/`.
- [ ] **S20** The supply chain is pinned loosely: actions by tag, not SHA; `python:3.12-slim` and `mongo:7` not by
  digest; pip by `~=` without hashes; releases push `latest`. `.github/workflows/`, the Dockerfiles, the
  `requirements.txt` files.
- [ ] **S21** The database checks nothing itself: the old installed MongoDB `$jsonSchema` validators at start; now
  only the code checks. `api_server/`.

## Correctness and performance

- [ ] **P1** When an agent asks the model and the provider fails with anything but CallFailed, Refused or Cancelled,
  the agent is never answered and its call hangs. `runtime/src/agents/functions.py` (`Functions.serve`).
- [ ] **P2** Usage leaves out prompt-cache reads and writes (though "input" excludes them), reasoning tokens, purpose
  and duration, so cost is under-reported. `runtime/src/chats/chat.py`, `shared/llm/`. The old: `llms/meter.py`.
- [ ] **P3** Each call reads the session, user, organization, groups and policies and writes a rate-limit counter,
  5–6 round trips with no cache; the old answered these from its `AccessCache`.
  `api_server/src/doors/callers.py`, `doors/limits.py`, `people/permissions.py`.
- [ ] **P4** N+1: `Bounds.keeps_access` evaluates the rules for every active person on each group, user or policy
  change; `NodeEndpoints.list` asks for each runtime's chats one runtime at a time.
  `api_server/src/people/permissions.py`, `api_server/src/runtimes/endpoints.py`.
- [ ] **P5** Each chat event, streamed text too, is a find and an insert, and every API server reads every event from
  one unfiltered change stream. `api_server/src/chats/store.py`, `chats/socket.py`. Fine at modest scale; grows with
  servers × events.
- [ ] **P6** No warm processes: each new chat starts its agents cold (kept 12 idle minutes). Each line a process writes
  is its own mTLS call, sent one at a time; a program's first pull is pieces one after another.
  `sandbox/src/process.py`, `sandbox/src/callers.py`, `runtime/src/utils.py`.
- [ ] **P7** Every agent's full instructions, and up to 100 tool schemas, go into every system prompt; the old
  shortlisted agents with an embedding router and rerank (see F6). `runtime/src/mind/mind.py`.
- [ ] **P8** History is folded at a fixed 150,000 characters, whatever the model's window.
  `runtime/src/mind/history.py`.
- [ ] **P9** Heavy work on the event loop (`str(state)` again and again while trimming; base64 of up to 16 pictures
  of 5 MB), and caches never pruned (pictures, `Sandboxes.offered`). `runtime/src/mind/mind.py`,
  `runtime/src/agents/functions.py`, `runtime/src/sandboxes/sandboxes.py` (filled from `agents/process.py`).
- [ ] **P10** The sandbox overcommits memory (32 processes × 1 GiB in a 4 GiB container) and nothing shields its
  supervisor from the kernel's OOM killer. `sandbox/src/__main__.py`, `sandbox/src/watch.py`.

## Tests and CI

- [ ] **T1** The runtime has 32 tests and none for a model connector, the hand-written Bedrock event-stream parser
  among them (the old: 1,198, connectors included).
- [ ] **T2** The API server, issuer, communication and shared have 230 tests (the old Data Core: 1,160); nothing tests
  OAuth, the outside-call client, key rotation, notifications, or more than one server.
- [ ] **T3** The old's 94 hostile-agent tests (`ai_runtime/tests/test_confinement_live.py`,
  `test_spawner_live.py`) are not ported to the sandbox's tests.
- [ ] **T4** CI passes when every sandbox test is skipped (`sandbox/tests/test_sandbox.py`, `skipif`); the old
  failed the run when nothing in its live files ran. `.github/workflows/tests.yml`.
- [ ] **T5** The whole-deployment test is left out of CI, and CI starts a three-member replica set for cluster tests
  that no longer exist. `.github/workflows/tests.yml`.
- [ ] **T6** No CI job checks the published agents (the old had one).

## Features the old had

Each needs a decision: port it, or strike it here with the reason.

- [ ] **F1** Azure Blob and Google Cloud Storage for files (now local and S3).
- [ ] **F2** An organization's own node authority, by its own CA or JWKS (now only the deployment's issuer).
- [ ] **F3** `AI:Storage`: verified function results kept and referenced by `storage_ref`, for tables and graphs.
- [ ] **F4** `IAM:Role`: policies bundled into roles (now groups map straight to policies).
- [ ] **F5** `Secrets:Definition` and `Secrets:Credential`: an organization's secret definitions and starter ones
  (now only per manifest).
- [ ] **F6** `Settings:Routing`, the embedding router and `find_agents`: shortlisting and reranking agents, tunable
  per organization.
- [ ] **F7** `Settings:Safety`: how often code cards ask, and which packages a program may install.
- [ ] **F8** The model reviewing proposed code before its approval card; `call.propose` and `call.install`.
- [ ] **F9** Grant constraints on values (the old `execution/grants.py`).
- [ ] **F10** Plans backed by evidence, and tables attached only when the trace proves them (the old `mind/plan.py`,
  `mind/evidence.py`).
- [ ] **F11** Reading documents' text (PDF, CSV, Markdown), `read_file`, and `find_files` with its ranking card.
- [ ] **F12** `recall`, `read` (paging through stored results), `sleep`, `unschedule`.
- [ ] **F13** Scheduled runs that wake the assistant on an interesting result (`wake_field`), and asking a person for
  a login (`call.credential`).
- [ ] **F14** A browser in the sandbox (Chromium), and watching its screen before it is asked for.
- [ ] **F15** Building an agent from its manifest in the sandbox: pip builds as a builder user, a verification start,
  extras per run (now programs come prebuilt for each machine type).
- [ ] **F16** Monitoring: an event history, usage and CPU per agent, disk measured, a home's files listed, and reports
  to monitoring (now memory and task counts only).
- [ ] **F17** One sandbox serving several runtimes (drain, labels, recheck every 30 s), and sandboxes that only dial
  out, so one can sit behind NAT.
- [ ] **F18** The assistant's prompts as files an operator can edit (the old had 8 `.md` prompts; now in the code).
- [ ] **F19** Carrying data over from before the redesign ([`deploy/README.md`](../deploy/README.md) says it is not).
