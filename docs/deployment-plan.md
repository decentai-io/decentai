# Deployment: the plan

*Agreed 19 October 2026. Kept as the record of what was decided about
how DecentAI reaches people: the clients, who runs the engine for
them, and the rule that keeps the engine out of that question. How a
server is stood up today is [deploying](run/deploying.md); the engine's
settings are [configuration](run/configuration.md).*

**The core rule:** the engine and the web client are deployment
neutral. They ship as code and images, configured by environment and
one runtime file, and nothing in either knows where it runs. Every
"how to run it somewhere" is a recipe kept apart from the code.

---

## 1. What we agreed

**The engine is headless.** It is an API at one address, plain HTTP on
one port, and that is its whole contract with the world. No reverse
proxy, no TLS, no page served. What sits in front of it — Caddy on a
small server, a load balancer in a cloud, an ingress in a cluster,
nothing at all on a laptop — is the deployment's choice, and the engine
is told only what it needs to know: the origins it may answer
(`CORS_ALLOW_ORIGINS`), its own public address, and which proxies to
believe about a client's address.

**Three clients, every one thin at the door.** A person opens a client,
enters a server's address, an email and a password, and is in. No
client sets an organization up: registration and the organization are
the business of whoever operates the engine — a person on their own
computer, an organization's IT, a provider — never the client's.

| Client | Kind | Runs on | Distributed as |
|---|---|---|---|
| Mobile | thin | iOS, Android | the App Store and Google Play, built with EAS |
| Web | thin | any browser | a static bundle and a container image on its release; not hosted by the project |
| Desktop | thick, with a thin mode | Windows, macOS | a signed installer on a GitHub release |

**The web client is files.** It is built once into static files, and
those files go wherever a web client is wanted: inside the desktop
app, in its own container beside an engine, on any static host. It is
told the engine's address by a runtime config file beside the files,
or by the person at the first screen, and it signs in with the token
the Data Core returns, carried as a bearer header — the same way the
phone does. The project runs no public copy of it. The one case where
it would is the one where the project is itself a provider (section 3).

**The desktop app is the way a person runs DecentAI on their own
computer.** It is a small shell holding the web client's files, and
on first open it asks one question: run DecentAI here, or connect to a
server. *Connect* is the thin mode — the bundled web client, pointed at
an address. *Run here* is the launcher: it reads a signed release file,
downloads the engine images that file names by digest, and starts,
stops, updates and removes them through Docker or Podman, then opens
the same web client at `localhost`. The person installing it is the
operator, so the first-run form that names the organization and the
first account lives there, and nowhere else. The installer is small;
the images arrive on first run.

Not a native bundle: the sandbox isolates each agent in a container of
its own, the Data Core needs its database, speech runs in a container.
"Thick" means a shell that manages a container stack on this machine,
and the one real cost of it is containers on a laptop — finding Docker
or Podman, offering to install Podman where there is neither, WSL2 and
an administrator's prompt on Windows, a virtual machine on macOS. The
first run is designed around that: clear states, a visible log, and a
retry that leaves nothing half done.

**Recipes live apart from the code.** A repository of their own,
`decentai-deploy`, one folder per way of running it, each pinning the
image versions a release published. The engine repository keeps a
Dockerfile per component and the environment template; its setup
script keeps the engine's own jobs — keys, the environment file, the
first run's seeding — and starting a stack is a recipe's job. The
desktop launcher is the desktop repository's own recipe and stays
there. A provider or an operator forks a recipe, never the engine.

| Recipe | For | What it holds |
|---|---|---|
| `local-dev` | a developer on their own machine | compose for the engine, the web client's development server, the commands |
| `single-host` | a server with a domain: a rented VM, a home server, a NAS | compose with the engine, the web client's container, and a TLS terminator in front |
| `node` | an execution node on a machine of its own, for a Data Core elsewhere | compose for the runtime, its sandbox and the node's issuer |
| `kubernetes` | an organization's own cluster | a Helm chart; ingress and SSO notes |
| `cloud-vm/aws`, then `azure`, `gcp` | a provider or an organization on a cloud | infrastructure code: one VM, or two (the Data Core, and agents apart) |
| `marketplace` | one-click listings | per-marketplace templates, generated from `single-host` |

## 2. Who runs the engine

A *provider* is whoever operates an engine that thin clients connect
to. By who runs the machines there are seven, in three groups, and
every one runs the same images from the same release file. The
provider question is operations and packaging; it never forks the
engine.

**The person is the provider.**

| Provider | What it looks like | Who serves the web client |
|---|---|---|
| Own computer | the desktop app; a phone reaches it only through a tunnel such as cloudflared or Tailscale | the desktop app, bundled |
| Home server or NAS | an always-on box at home, reached from the phone over Tailscale | the web client's container on the box |
| Own server | a rented VM on a domain, the `single-host` recipe | the web client's container beside the engine |

**An organization is its own provider.**

| Provider | What it looks like | Who serves the web client |
|---|---|---|
| Organization IT | on-premises or a private cloud, behind SSO, perhaps air-gapped; a cluster more often than compose | the organization |
| Cloud marketplace | a one-click listing; the cloud hosts, the customer operates | the template, as part of the stack |

**Someone else is the provider.**

| Provider | What it looks like | Who serves the web client |
|---|---|---|
| DecentAI managed | the project runs engines for customers: one engine each, or one Data Core serving many organizations. Billing, support, residency — software becoming a service, decided deliberately | the project, at an app address of its own |
| A partner | a consultancy or a host runs engines for its clients, often under its own name | the partner |

**Two things the architecture allows across every row.** Execution
nodes dial in to the Data Core
([execution nodes](system/execution-nodes.md)), so the halves can live
apart: a provider hosts the Data Core while the customer keeps
execution nodes on its own machines, so agent code and secrets never
leave the building; or the Data Core stays on premises for the data's
sake while heavy execution runs on a provider's nodes. And the engine
is one thing everywhere: a single machine is the smallest deployment,
not a special design.

## 3. Signing and stores

| Target | Identity | Where it is used |
|---|---|---|
| iOS | an Apple Distribution certificate, made and kept by EAS | the App Store, by way of TestFlight |
| Android | an upload keystore kept by EAS; Play App Signing from the first upload | Google Play, a closed test track first |
| macOS | a Developer ID Application certificate and notarization, from the same Apple membership as iOS | the desktop release workflow, on a macOS runner |
| Windows | a code-signing certificate of its own, through a cloud signing service that a workflow can reach | the desktop release workflow; last |

Both Apple identities come from one membership. Windows is the
separate purchase; until it is made the installer runs, with
SmartScreen's warning on first open.

## 4. Releases

Each repository tags a version. The engine's and the web client's
workflows build images and publish them to the registry. The desktop
repository's workflow writes the release file that names those images
by digest, signs it with the key whose public half the launcher and
the app carry, builds and signs the installers, and attaches all of it
to a GitHub release. Recipes pin to the versions released. The mobile
app releases through EAS on a cadence of its own.

## 5. The order of work

1. The engine headless and the web client standalone: the engine's
   gateway removed, the web client given an engine address and bearer
   sign-in, its own container and content-security policy.
2. Release workflows for the engine and the web client, publishing
   images.
3. `decentai-deploy` with `local-dev` and `single-host`. The second
   also stands up the engine the stores' reviewers sign in to.
4. The desktop repository cut down to the shell and the launcher,
   consuming the released images; macOS signing and notarization in its
   workflow.
5. The mobile app to both stores.
6. `kubernetes`, the clouds, the marketplace templates.
7. Windows signing.

*Where it stands (21 October 2026).* 1 is done. 2's workflows are
written and no release has been cut yet. 3 is done, with `node` and
`cloud-vm/aws` beside it (decentai.net runs on the latter, from images
built locally). 4 is done in its code; its first release waits on 2. 5
to 7 are to come, Kubernetes set aside for now. `bootstrap/setup.py`
still starts the stack on a person's own computer, beside making its
keys and its settings.
