# The desktop install

DecentAI on one person's own computer. What is installed is the same
platform a server runs — the same images, the same code — with a
different front: one address, `http://localhost:4280`, reachable from
this computer only. A person on their own is an organization of one,
and its administrator. [Installing it](../guides/desktop.md) is the
how-to; this page is how it works.

## Why containers

The backend needs a MongoDB server, the runtime carries a browser, and
every part has to be updated on every machine it was ever installed
on. Containers answer all three: MongoDB runs in its own container,
the parts ship as images, and an update is a pull of new images, the
same on every operating system.

| Boundary | Held by |
|---|---|
| Agent code reaching the person's files, browser profiles and saved passwords on the machine | the container |
| One agent reaching another's files, the network beyond its declared hosts, or more than its share | [the sandbox](sandbox.md), inside the runtime's container |

The sandbox's firewall rule needs the container started with one
right, `NET_ADMIN`, which the launcher's Compose file grants.

## The launcher

`launcher/`. What an install does — the first run, a start, an update
and its way back — is written in Python and runs in a container of its
own, handed the engine's socket and a volume to keep its settings in
(`/state`). So it needs the engine and nothing else of the machine, and
it is updated the way the platform is, by pulling an image.

    docker run --rm -it \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v decentai_launcher:/state \
        decentai-launcher install

The launcher's container is given the engine, and whoever has the
engine has the machine's containers. It is the platform's own code, and
it runs only while it is asked something.

| Command | Does |
|---|---|
| `install` | the first run |
| `start`, `stop` | every day; nothing is removed by a stop |
| `status` | what is installed, and what is running |
| `update` | a newer release, after asking (`--yes` does not ask; `--again` reinstalls the same version) |
| `backup` | a copy of the database, now |
| `stop-everything` | starts the runtime's container again, ending everything the agents are doing — the way out when the runtime itself no longer answers, which a chat's own Stop cannot be |
| `reset-password` | a new password for a person of the install (`--email`, or the first person), set by the platform's own script (`bootstrap/reset_password.py`) in the backend's image: what a reset link does, for an install with no email to send one. The password is said once and kept nowhere |
| `develop <folder>` | hands a folder of the person's own to the backend, read-only, as `/develop` (below); `develop --off` takes it back |

**The first run:**

1. Checks the email and password of the first person — a letter and a
   number, ten characters or more. They are said once, to the
   seeder, and kept nowhere.
2. Pulls the release's images. A developer's own build, already on the
   machine, is used as it is.
3. Makes the install's keys — the service signing pair, the encryption
   keys and the session secret — with the platform's own generators,
   run from the release's backend image, and writes them with the
   database's password into the launcher's volume. Nothing is typed.
4. Runs the seeder, which creates the organization and the first
   person, then starts the stack and waits until the backend, the
   runtime and the web server are healthy.
5. Records the version and images it installed, and returns the
   address.

The launcher's Compose file (`launcher/compose.yml`) names its project
`decentai-app`, so its volumes never meet a developer's own stack
started from a folder called `decentai`.

**Where the keys live.** In the launcher's volume, with the settings it
made. A copy of the database is unreadable without them, and the
launcher does not yet take the two out together.

### Which engine

The platform needs a container engine and not one in particular: the
launcher is handed a socket, and the same images run on both.

| On the machine | The starter uses |
|---|---|
| Docker, running | Docker |
| Docker, not running, or no engine | Podman, installed if it is not there |

Docker where it runs, because the person chose it and a second engine
costs disk and memory. Podman everywhere else, because it is free for
a person and for a company of any size, it can be installed for the
person, and it runs without root: root in a container is an ordinary
user of the machine. Which engine an install was made on is kept with
the install, and an install is never begun again on the other: where
its engine does not start, the starter says so and stops.

### The starter for Windows

`launcher/starter/windows`: a PowerShell script, and `DecentAI.cmd`
beside it that a person double-clicks. PowerShell because every
Windows carries it, so the starter needs nothing installed and can be
read by whoever wants to know what it does.

| It | How |
|---|---|
| chooses the engine | by the table above; asking which one installs and starts nothing |
| starts Docker Desktop | where an install was made on Docker and Docker is not running; it never installs Docker |
| installs Podman | with `winget`, after asking; without `winget` it says where Podman is |
| makes and starts Podman's machine | once, and at every start after that |
| brings the Windows Subsystem for Linux up to date | only when Podman's machine did not start and the subsystem is older than the version it is known to start on, after saying that every container stops meanwhile and asking |
| remembers the engine | in `%LOCALAPPDATA%\DecentAI\starter.json`, once an install or a start has worked |
| runs the launcher | handed the chosen engine's socket, with the person's window for what the launcher asks |
| shows DecentAI in a window of its own | the person's own browser where it can show one page as a window (Chrome, Edge, Brave and their kin), Edge where it cannot, and a tab where there is neither |
| offers a shortcut | on the desktop and in the Start menu, asked once when somebody is there to answer; `DecentAI.cmd shortcuts` makes them later |
| translates `develop <folder>` | into the path the engine sees: `/run/desktop/mnt/host/c/…` on Docker Desktop, `/mnt/c/…` on Podman |

### The starter for macOS

`launcher/starter/macos/DecentAI.command`: one bash script, for the bash
every Mac carries, which Finder opens in Terminal with a double-click
and which runs as `./DecentAI.command <command>` from Terminal. It
does what the Windows starter does, the Mac's way:

| It | How |
|---|---|
| chooses the engine | by the same table; asking which one installs and starts nothing |
| starts Docker Desktop | where an install was made on Docker and Docker is not running; it never installs Docker |
| installs Podman | with Homebrew, after asking; without Homebrew it says where Podman is |
| makes and starts Podman's machine | once, and at every start after that |
| remembers the engine | in `~/Library/Application Support/DecentAI/starter.conf` |
| finds the engine's programs | on PATH, or where Docker Desktop, Podman's installer and Homebrew put them — an app opened from Finder is given only the system's own folders |
| shows DecentAI in a window of its own | the person's own browser where it is of the Chrome family (read from what macOS records as the handler for the web), else the first of that family installed, and a tab of whatever browser there is where there is none |
| offers an app | `DecentAI.app` in `~/Applications`, asked once: it runs this starter, wears the DecentAI mark (made with the Mac's own `sips` and `iconutil` from `DecentAI.png` beside the script), and writes what happened to `~/Library/Logs/DecentAI.log`, with an alert when DecentAI did not open |
| hands over `develop <folder>` | as it is: Docker Desktop and Podman's machine both share the home folders under the same paths; a folder outside them is refused |

The app is made for the checkout it was made from; moved, it is made
again with `./DecentAI.command shortcuts`.

The address is plain HTTP on `localhost`. A browser treats `localhost`
as it treats a secure address, so the microphone, the camera and the
installed app work.

## A release

One version number names three images — backend, runtime, frontend —
in one release file (`launcher/release.json`):

```json
{
  "schema_version": "1.0",
  "version": "1.4.0",
  "released_at": "2026-10-10T08:00:00Z",
  "images": {
    "backend":  "…/decentai-backend@sha256:…",
    "runtime":  "…/decentai-ai-runtime@sha256:…",
    "frontend": "…/decentai-caddy@sha256:…"
  },
  "minimum_launcher": "1.0.0",
  "notes": "…"
}
```

- **The three move together**, so a new page never meets an old
  backend.
- **The file is signed**: Ed25519 over its exact bytes, kept beside it
  as `<file>.sig`. Whoever could put a release file in front of an
  install could put their own platform on the machine, so a file is
  believed for its signature and not for where it came from. The
  launcher carries the public keys it accepts in `launcher/keys/` —
  two, so that a lost key is not a launcher nobody can update.
- **A release that is not signed** is a developer's own build,
  installed only when the person says `--unsigned`
  (`DECENTAI_UNSIGNED=1` for the starter).

### How one is published

A version is tagged (`v1.4.0`) and the release workflow
(`.github/workflows/release.yml`) does the rest:

1. builds the backend, runtime and web images for both processor types
   a laptop has (Intel/AMD and ARM) and pushes them to the registry;
2. writes the release file naming each by its digest, signs it
   (`python -m launcher.publish release`), and reads it back with the
   keys in `launcher/keys/` — a release signed with a key no launcher
   accepts stops here;
3. builds the launcher image, carrying that file and the address later
   releases are published at;
4. packs the starters, with the launcher's image written into them by
   its digest;
5. publishes the starters, the release file and its signature on the
   repository's releases page.

A published launcher reads the current release from that page
(`DECENTAI_RELEASE_URL`, set when its image is built) and believes it
for its signature, so `update` finds a newer release without a new
starter. A release that needs a newer launcher says so
(`minimum_launcher`), and the newer launcher comes with the current
starter.

What the signature rests on: the release key's private half is a secret
of the repository on GitHub, so that the workflow can sign. A release
is therefore as trustworthy as that account. The launcher's own image
is believed for its digest, which the starter carries, and the starter
for where it was downloaded.

A checkout's own `launcher/release.json` names images built on the
machine (`decentai-backend:local` and its kin) and carries no
signature: a build of one's own.

## An update

1. A release that is not newer than what is installed is refused,
   unless `--again` says to reinstall it.
2. The new images are pulled while the old ones still run.
3. The database is copied; the last three copies are kept.
4. The stack stops, the install's settings are brought up to date — a
   setting a newer platform expects is added, and none that is there is
   changed — and the seeder runs under the new images. It finds the
   first person and creates nobody.
5. The new version starts, and the launcher waits up to five minutes
   for it to be healthy.
6. If it does not come up, the previous images are put back and the
   copy of the database restored.

Only changed layers are downloaded, and the runtime image copies its
code last, so the browser inside it is not downloaded again.

**An update never touches** uploaded files, approved agent packages,
agent environments or the keys, and the database only through the
seeder. **Agents are updated separately**, on the marketplace.

An update asks before it starts, and interrupts whatever is running.

## A folder to develop agents in

`develop <folder>` writes an override beside the launcher's Compose
file that mounts the folder into the backend, read-only, at `/develop`,
and sets `AGENT_SOURCE_FOLDER=/develop`. The git repositories in it may
then be added on the marketplace as sources by their path — `/develop`
itself, or `/develop/<a folder>` — without pushing them anywhere. The
backend refuses a path outside that folder and a folder that is not a
git repository. The folder stays handed over across starts and updates
until `develop --off`. The agent template's developing guide walks the
loop: commit, Refresh the source, take the Update.

## What a desktop is told

The backend is told what the deployment is by one setting,
`DEPLOYMENT_KIND`: `web`, served to an organization at an address of
its own, or `desktop`, on one person's computer. A deployment that does
not say is a web one. The launcher writes `desktop` into the settings
of every install it makes, and adds it to an older install at its next
start or update. Every screen is told with the person's own identity.

What differs is a connected app. A provider issues a secret to an app
that runs on a server, where it can be kept. An app registered for a
person's computer is given none by some providers, and what stands in
its place is the one-time value every sign-in already carries (PKCE).
So on a desktop:

| | Web | Desktop |
|---|---|---|
| A connected app's secret | required | kept when given, not required |
| The form, for Microsoft and Dropbox | id and secret | the id alone |
| The form, for a provider that hands every app a secret | id and secret | id and secret |
| The form, for a provider the page does not know | id and secret | id, and a secret that may be left empty |
| The exchange, for an app without a secret | | names the app by its id, and sends no secret, not even an empty one |

The person registers the apps under **Settings → Connected apps**. A
provider that refuses a redirect address that is not HTTPS — Slack is
believed to be one — cannot be connected from a desktop.

## Without the launcher

The Compose file and the release file are plain files. Whoever prefers
a command line runs them by hand and gets the same install; the
[quickstart](../quickstart.md) is the server's way.

## What it does not do

- **A starter for Linux** is not written; the launcher's container runs
  there, started by hand.
- **The starters are not signed** for Windows or macOS, so each system
  asks once whether to run them.
- **The launcher does not update itself**: a newer one comes with the
  current starter.
- **Nothing checks for an update by itself**; `update` is run when the
  person asks.
- **An update does not wait** for chats to finish.
- **Schedules and watches do not run while the computer sleeps.**
- **Installing an engine** needs the Windows Subsystem for Linux and
  administrator rights once, which a locked-down work laptop may
  refuse.
