# Known gaps

What the redesigned platform still has to fix, test or decide, found on 2026-11-10 by comparing it with the platform
before the redesign (`decentai-io/decentai` at `007e4da`, 2026-10-26, kept in the offline archive
`decentai_archive_old.zip`; "the old" below). Each item has an ID to refer to it by in commits and issues; tick it
when it is done, or strike it with the reason when it is dropped on purpose.

Items marked **(checked)** were confirmed in the code when this list was written; the others come from a review of
the code and are confirmed first when their turn comes. Places are as of the commit that added this file.

Every item was decided on 2026-11-11: ticked ones are done, struck ones dropped with the reason, and the open ones,
which matter with scale, are for later.

## Security

- [x] **S1 (checked)** MCP tool calls pass no gate: no approval card, no trust level, no check of their inputs. A turn
  begun by a webhook or a schedule, with the outside text in the prompt, can make side-effecting MCP calls.
  `runtime/src/mind/brain.py` (`use`) → `runtime/src/chats/chat.py` (`mcp`). *Do:* send MCP calls through the gate as
  agents' calls are; the old did (`ai_runtime/agents/mcp.py`). *Done:* Each MCP tool has a level, 2 unless its person
  changes it, weighed against the chat's trust as an agent's function is. The organization's `webhook_trust` is gone;
  each webhook keeps its own trust.
- [x] **S2 (checked)** An approval is reusable. `Gate._approved` takes any answered card in the chat whose call id
  matches the one the caller gives; the card is not bound to the agent, function, resource or operation, and is never
  spent. `api_server/src/records/agents.py`. *Do:* bind the card to what it approved and spend it once. *Done:* A card
  lets a call through only for its call, agent, function and operation, and only once; call ids start at random.
- [x] **S3 (checked)** An enrollment token an administrator makes has unlimited uses unless they set `uses`, for 7
  days, and its certificates may live up to the intermediate's 120 days. `issuer/controllers.py` (`create`),
  `api_server/src/runtimes/endpoints.py`. *Do:* default to one use and a short life. *Done:* A token an administrator
  makes is for one node unless they say; the web client asks for a number.
- [x] **S4** The issuer does not ask whether a node was refused: a refused runtime keeps making sandbox tokens and
  enrolling sandboxes until its certificate expires (12 h). Only renewal checks. `issuer/controllers.py`
  (`Rules.allowed`, `TokenController.create`). *Do:* check refusal on every token and enrollment. *Done:* A refused
  node makes no tokens, and a token it launched enrolls nobody.
- [x] **S5 (checked)** The way out forwards a plain-HTTP request without rewriting its `Host` header, so a declared
  host on a shared address (a CDN) reaches other sites on it. Its copy loop has no idle timeout, and no connection is
  recorded. `runtime/src/way_out/proxy.py`. *Do:* set `Host` from the checked target, add an idle timeout, record each
  connection; the old did all three (`ai_runtime/agents/egress.py`). *Done:* A connection idle both ways for 5 minutes
  is closed, and each one is a log line. The `Host` header is written down in runtime.md §7 instead, since HTTPS has
  the same limit and can't be rewritten.
- [x] **S6** `FileDoor.piece` serves any offered file, and any package by digest, to any sandbox that asks; it is not
  bound to the sandbox it was offered to. `runtime/src/sandboxes/sandboxes.py`. *Do:* a ticket per offer, as the old
  had (`ai_runtime/agents/sandboxes.py`). *Done:* Offers are keyed by sandbox, and withdrawn when the process ends.
- [x] **S7 (checked)** The sandbox container's seccomp profile allows by default (`SCMP_ACT_ALLOW`) and denies about
  30 calls; the old kept Docker's default allowlist. `sandbox/config/seccomp.json`. *Do:* start from Docker's default
  and add only what nsjail needs. *Done:* Docker's default (moby v27.5.1), with what nsjail needs and without what the
  sandbox refuses. Proven once the sandbox's tests pass with it.
- [x] **S8 (checked)** The sandbox runs with `systempaths=unconfined`, and each jail mounts a fresh `/proc`, so
  agents' code reads the host's world-readable `/proc` that Docker would mask (`/proc/timer_list` and others).
  `deploy/docker-compose.yml`, `sandbox/config/jail.cfg`. *Do:* mask those paths inside the jail. *Done:* Covered in
  `jail.cfg`. Proven once the sandbox's tests pass with it.
- [x] **S9** The compose file sets no AppArmor profile, though [sandbox](sandbox.md) §9 requires
  `apparmor=decentai-sandbox`; on Ubuntu the jail's probe then fails and an operator is tempted to `unconfined`. The
  profile given is itself broad (`capability, file, mount, userns`). *Do:* set it in the compose file and narrow it.
  *Done:* The profile allows the capabilities nsjail uses by name, the network families the sandbox uses, and no mount of a file system that reaches the host's kernel or another machine; CI loads it and runs the sandbox's tests under it on Ubuntu 24.04. The compose file stays as it is: without the profile the sandbox refuses to start rather than run unconfined, and naming it would break every host that hasn't loaded it.*
- [x] **S10** The jail's own seccomp list misses calls the old blocked: `open_tree`, `move_mount`, `fsopen`,
  `fsmount`, `fspick`, `mount_setattr`, `pidfd_getfd`. `sandbox/config/jail.cfg`. *Do:* add them (defence in depth; no
  capabilities and the user-namespace block cover them today). *Done:* In `jail.cfg`. Proven once the sandbox's tests
  pass with it.
- [x] **S11** The jail sees the container's `/etc`; nothing caps the disk `/data/programs` uses; the sandbox has no
  healthcheck. `sandbox/config/jail.cfg`, `sandbox/Dockerfile`. *Done:* A healthcheck. Dropped: narrowing `/etc`,
  which holds nothing secret and which programs need; a disk cap, since unused programs are removed after `kept_days`.
- [x] **S12 (checked)** `deploy/config/deploy.env.example` writes `change-me` for the database passwords
  (`MONGO_ROOT_PASSWORD`, `AI_API_DATABASE_URI`, `ISSUER_DATABASE_PASSWORD`), and nothing refuses them; only the
  encryption key is checked. `setup.py` replaces them, a hand-made file may not. *Do:* refuse `change-me` at start.
  *Done:* The API server and the issuer refuse to start with `change-me`, the setup code too.
- [x] **S13** The outside-call check (SSRF) does not treat NAT64 addresses (`64:ff9b::/96`) as private; the old did.
  `api_server/src/utils.py`. *Done:* In the API server and the way out; `shared/mcp.py` had it already.
- [x] **S14** The issuer writes its intermediate's private key unencrypted (mode 0600), and the first run's setup code
  is written to the log. `issuer/authority.py`, `api_server/src/__main__.py`. *Done:* The key is encrypted with
  `ISSUER_KEY_PASSPHRASE`, and one written before is encrypted when first read. The setup code is required until the
  first run is done, and never logged.
- [x] **S15** Rate limits are by client address: with `AI_API_PROXIES` wrong, everyone behind one proxy shares one
  sign-in bucket (20 tries in 15 minutes). `api_server/src/doors/limits.py`. *Do:* say so in
  [operating](operating.md), and warn at start when requests come only from a proxy that is not listed. *Done:*
  operating.md says so already: step 4 of a server, and its troubleshooting.
- [x] **S16** A picture's type is taken from its file name, not its first bytes; the old checked the bytes.
  `runtime/src/agents/functions.py`. *Done:* Checked by its bytes, with the API server's check, now in
  `shared/file_types.py`.
- [x] ~~**S17** The `fake` model provider can be chosen in a chat's settings in production.
  `runtime/src/models/factory.py`. *Do:* allow it in tests only.~~ *Dropped:* The API server refuses a provider
  outside the catalog, so no chat can use it, and it reaches no network.
- [x] **S18** Checking a model connection echoes up to 500 characters of the endpoint's error, and model endpoints are
  not held to public addresses (the old was the same). `runtime/src/api/endpoints.py`. *Done:* A model endpoint must
  be public unless its host is in `AI_RUNTIME_INSIDE_HOSTS`. The error's 500 characters stay, for debugging.
- [x] ~~**S19** The speech service asks no one who is calling, on the stack's default network. `speech/`.~~ *Dropped:*
  Only the API server and the runtime share its network, and it isn't published.
- [x] **S20** The supply chain is pinned loosely: actions by tag, not SHA; `python:3.12-slim` and `mongo:7` not by
  digest; pip by `~=` without hashes; releases push `latest`. `.github/workflows/`, the Dockerfiles, the
  `requirements.txt` files. *Done:* Actions pinned by commit, base images by digest, every Python package by version and hash (`requirements.txt`, made from `requirements.in`, installed with `--require-hashes`); releases push only their version. Dependabot keeps all four current.*
- [x] ~~**S21** The database checks nothing itself: the old installed MongoDB `$jsonSchema` validators at start; now
  only the code checks. `api_server/`.~~ *Dropped:* The code is the one place shapes are checked; validators would be
  a second copy to keep in step.

## Correctness and performance

- [x] **P1** When an agent asks the model and the provider fails with anything but CallFailed, Refused or Cancelled,
  the agent is never answered and its call hangs. `runtime/src/agents/functions.py` (`Functions.serve`). *Done:* Any
  other failure answers the program with `failed`.
- [x] **P2** Usage leaves out prompt-cache reads and writes (though "input" excludes them), reasoning tokens, purpose
  and duration, so cost is under-reported. `runtime/src/chats/chat.py`, `shared/llm/`. The old: `llms/meter.py`.
  *Done:* Cache reads and writes and reasoning are reported, kept and priced, and the usage page counts them. And
  (2026-11-12) each call's time, duration and purpose, shown when a chat is inspected.
- [x] **P3** Each call reads the session, user, organization, groups and policies and writes a rate-limit counter, 5–6
  round trips with no cache; the old answered these from its `AccessCache`. `api_server/src/doors/callers.py`,
  `doors/limits.py`, `people/permissions.py`. *Done:* Who calls is one read: the session or key, the person and the organization (its settings and access version with it); what the person's groups and policies allow is kept on each server by that access version, raised by every change to access, so it holds from the next call; how often people call is counted in memory and added once a second; the trail is written after the answer, a batch a second; a runtime's standing in a chat is kept 2 s. A person's read is 3 round trips, from 8; a change, 3 from 9; writing in a chat, 9 from 18 (measured with pymongo's command listener).*
- [x] **P4** N+1: `Bounds.keeps_access` evaluates the rules for every active person on each group, user or policy
  change; `NodeEndpoints.list` asks for each runtime's chats one runtime at a time.
  `api_server/src/people/permissions.py`, `api_server/src/runtimes/endpoints.py`. *Done:* Whether someone still manages access reads the organization's groups, policies and people once each; the node list counts held chats in one aggregation; refusing a runtime, and the sweep of runtimes gone, let their chats go in one write. The sweep now also resumes a working chat elsewhere, which it never managed before: it placed the chat as still held by the runtime gone.*
- [x] **P5** Each chat event, streamed text too, is a find and an insert, and every API server reads every event from
  one unfiltered change stream. `api_server/src/chats/store.py`, `chats/socket.py`. Fine at modest scale; grows with
  servers × events. *Done:* What only passes, streamed text and screens, never reaches the database: each server with the chat open asks its runtime for it (`Runtime:Chat:passing`), and the runtime streams twice a second. Kept events go through one change stream a server, projected and resumed where it stopped. A kept event is 2 round trips, from 6; a piece of streamed text, none.*
- [x] **P6** No warm processes: each new chat starts its agents cold (kept 12 idle minutes). Each line a process
  writes is its own mTLS call, sent one at a time; a program's first pull is pieces one after another.
  `sandbox/src/process.py`, `sandbox/src/callers.py`, `runtime/src/utils.py`. *Done:* A process sends at most 20 log lines a second, 200 at once; the rest are dropped and counted on the next line. Dropped: warm processes, which would share a process between chats; the first download, done once per sandbox.*
- [x] **P7** Every agent's full instructions, and up to 100 tool schemas, go into every system prompt; the old
  shortlisted agents with an embedding router and rerank (see F6). `runtime/src/mind/mind.py`. *Done:* Only the agents
  whose functions are offered have their instructions shown; past 100 functions, those opened.
- [x] ~~**P8** History is folded at a fixed 150,000 characters, whatever the model's window.
  `runtime/src/mind/history.py`.~~ *Dropped:* Folding at 150,000 characters keeps cost and answer time down on large
  windows, and a smaller window is folded on its first refusal.
- [x] **P9** Heavy work on the event loop (`str(state)` again and again while trimming; base64 of up to 16 pictures of
  5 MB), and caches never pruned (pictures, `Sandboxes.offered`). `runtime/src/mind/mind.py`,
  `runtime/src/agents/functions.py`, `runtime/src/sandboxes/sandboxes.py` (filled from `agents/process.py`). *Partly
  done by S6. Later: the rest.*
- [x] **P10** The sandbox overcommits memory (32 processes × 1 GiB in a 4 GiB container) and nothing shields its
  supervisor from the kernel's OOM killer. `sandbox/src/__main__.py`, `sandbox/src/watch.py`. *Done:* Each jail starts
  with `oom_score_adj` 1000.

## Tests and CI

- [x] **T1** The runtime has 32 tests and none for a model connector, the hand-written Bedrock event-stream parser
  among them (the old: 1,198, connectors included). *Done:* Pictures are encoded off the event loop, a picture no brain shows any more is let go, and the state is measured once while trimming, each trim saying what it freed. `Sandboxes.offered` by S6.*
- [x] **T2** The API server, issuer, communication and shared have 230 tests (the old Data Core: 1,160); nothing tests
  OAuth, the outside-call client, key rotation, notifications, or more than one server. *Done:* `runtime/tests/test_models.py`: one recorded stream for each protocol (Bedrock's event stream, Anthropic, OpenAI's chat and responses, Gemini), arriving in pieces that cut every frame, with text, thinking, a tool call and usage; and an error in the stream. Vertex reads with the Gemini and Anthropic connectors. And (2026-11-12) the API server's: `test_oauth.py` (the code exchanged once, for the person who started it, tokens sealed; a fresh access token for an agent, never the refresh token; nothing sent where the organization did not register), `test_outside.py` (only public addresses, each redirect checked again, a credential to its own host alone, size and deadline), `test_keys.py` (a value sealed under an older key opens until `admin reencrypt` seals it under the new one), `test_notify.py` (push or else email, nothing for a chat open in a browser, hidden text, an address's format, retries and pause), `test_servers.py` (an event kept through one server reaches a browser on another; a schedule, a notification and a placement done once by several servers). The change stream starts over from now after a drop invalidates it.*
- [x] **T3** The old's 94 hostile-agent tests (`ai_runtime/tests/test_confinement_live.py`, `test_spawner_live.py`)
  are not ported to the sandbox's tests. *Done:* `sandbox/tests/test_sandbox.py`, `TestAHostileProcess`: the old attempts that apply to a jail (kernel calls, sockets, mounts, devices, the sandbox's port, identity and files, the host's /proc files, signalling every process, a child left behind), with the OOM score that proves P10. The rest tested the old runtime's builder, pool and helper, which the redesign has no more.*
- [x] **T4** CI passes when every sandbox test is skipped (`sandbox/tests/test_sandbox.py`, `skipif`); the old failed
  the run when nothing in its live files ran. `.github/workflows/tests.yml`. *Done:* The job fails when the rig made
  no identities.
- [x] **T5** The whole-deployment test is left out of CI, and CI starts a three-member replica set for cluster tests
  that no longer exist. `.github/workflows/tests.yml`. *Done:* The API server's own rig runs it, a replica set of one,
  as CONTRIBUTING.md does; the whole-deployment test stays one run by hand.
- [x] ~~**T6** No CI job checks the published agents (the old had one).~~ *Dropped:* It belongs in the decentai-agents
  repository's CI.

## Features the old had

Each needs a decision: port it, or strike it here with the reason.

- [x] **F1** Azure Blob and Google Cloud Storage for files (now local and S3). *Done:* `AI_API_STORAGE=azure`, with a connection string, a container and a prefix; the admin tool copies files between any two storages. Google Cloud Storage works through its S3-compatible access.*
- [x] ~~**F2** An organization's own node authority, by its own CA or JWKS (now only the deployment's issuer).~~
  *Dropped:* An organization brings its own runtimes with tokens from the deployment's issuer; a second authority
  doubles what is trusted.
- [x] ~~**F3** `AI:Storage`: verified function results kept and referenced by `storage_ref`, for tables and graphs.~~
  *Dropped:* An agent shows its own table or chart with `chat`, or returns a file; `look` reads a long answer.
- [x] ~~**F4** `IAM:Role`: policies bundled into roles (now groups map straight to policies).~~ *Dropped:* A group
  bundles policies, as a role did.
- [x] ~~**F5** `Secrets:Definition` and `Secrets:Credential`: an organization's secret definitions and starter ones
  (now only per manifest).~~ *Dropped:* Each manifest declares the secrets it needs.
- [x] ~~**F6** `Settings:Routing`, the embedding router and `find_agents`: shortlisting and reranking agents, tunable
  per organization.~~ *Dropped:* P7 keeps prompts small, and `open` handles more than 100 functions, with no embedding
  model to run.
- [x] ~~**F7** `Settings:Safety`: how often code cards ask, and which packages a program may install.~~ *Dropped:*
  Programs come prebuilt and install nothing, and there are no code cards.
- [x] ~~**F8** The model reviewing proposed code before its approval card; `call.propose` and `call.install`.~~
  *Dropped:* The platform runs no model-written code; `code_runner` is an agent in its jail, gated by its level.
- [x] ~~**F9** Grant constraints on values (the old `execution/grants.py`).~~ *Dropped:* `scopes` in the rules narrow
  a function to the values allowed.
- [x] ~~**F10** Plans backed by evidence, and tables attached only when the trace proves them (the old `mind/plan.py`,
  `mind/evidence.py`).~~ *Dropped:* A table an agent shows comes from the agent itself.
- [x] ~~**F11** Reading documents' text (PDF, CSV, Markdown), `read_file`, and `find_files` with its ranking card.~~
  *Dropped:* The `documents` agent does it.
- [x] **F12** `recall`, `read` (paging through stored results), `sleep`, `unschedule`. *Done:* `sleep` and
  `unschedule`; `look` reads on through an answer with `from`, and recalls where words were said with `find`.
- [x] **F13** Scheduled runs that wake the assistant on an interesting result (`wake_field`), and asking a person for
  a login (`call.credential`). *Done:* A function's schedule may name a `wake` field. Dropped: asking for a login, now
  a `record.create` with empty fields.
- [x] ~~**F14** A browser in the sandbox (Chromium), and watching its screen before it is asked for.~~ *Dropped:* The
  browser agent carries its own Chromium, and its screens reach the person as notes.
- [x] ~~**F15** Building an agent from its manifest in the sandbox: pip builds as a builder user, a verification
  start, extras per run (now programs come prebuilt for each machine type).~~ *Dropped:* Programs come prebuilt for
  each machine.
- [x] **F16** Monitoring: an event history, usage and CPU per agent, disk measured, a home's files listed, and reports
  to monitoring (now memory and task counts only). *Done:* Each process's processor use, in cores, from the sandbox to the monitoring page; and `/metrics` in Prometheus' format, behind the operator's `AI_API_METRICS_TOKEN`. The rest is in api-server.md §9.*
- [x] ~~**F17** One sandbox serving several runtimes (drain, labels, recheck every 30 s), and sandboxes that only dial
  out, so one can sit behind NAT.~~ *Dropped:* Several runtimes already share a sandbox, and drain works; one behind
  NAT would need standing connections, removed on purpose.
- [x] ~~**F18** The assistant's prompts as files an operator can edit (the old had 8 `.md` prompts; now in the
  code).~~ *Dropped:* The organization's `guardrail` carries its own rules and tone.
- [x] ~~**F19** Carrying data over from before the redesign ([`deploy/README.md`](../deploy/README.md) says it is
  not).~~ *Dropped:* A fresh start, by choice.
