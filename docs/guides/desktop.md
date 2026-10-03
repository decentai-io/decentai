# On your own computer

DecentAI for one person, on Windows or a Mac, reachable from this
computer only. How it works underneath is
[the desktop install](../system/desktop-install.md).

## What you need

- **Windows 10 or 11**, with Docker Desktop, or nothing: where neither
  Docker nor Podman is there, the app offers to install Podman with
  `winget`. That asks for your permission once.
- **A Mac** (Apple silicon or Intel), with Docker Desktop, or nothing:
  the app offers to install Podman with Homebrew, or says where to get
  it.
- A key for the AI model you choose — Anthropic, OpenAI, OpenRouter,
  Gemini, any other provider the app lists, or another
  OpenAI-compatible service, or a model you host yourself.

## Install

Download the app from the [latest release](https://github.com/decentai-io/decentai/releases/latest):
`DecentAI-Setup.exe` for Windows, `DecentAI.dmg` for a Mac. Open it and
it installs like any program, with DecentAI in the Start menu (or the
Applications folder).

The installers are not signed for Windows or macOS yet, so the system
asks once whether to run them. On Windows choose **More info → Run
anyway**. On a Mac the first open is refused: click **Done**, open
**System Settings → Privacy & Security**, and under *Security* choose
**Open Anyway**.

Open DecentAI. It looks for Docker or Podman, starts the one it finds,
and shows a form: your email and a password (ten characters or more,
with a letter and a number), checked as you type. **Install DecentAI**
downloads it, sets it up and starts it; **Open DecentAI** then shows it
in a window of its own, at `http://localhost:4280`.

The first start downloads the images, which takes a while: most of it is the
browser the Browser agent drives; later updates download only what
changed.

## The first things to do

1. **Settings → Model providers → Add a provider.** Choose the
   provider and paste its key; it is tried before it is saved. One key
   serves every model the provider has, and the model is chosen in the
   chat. Keys are write-only.
2. **Agents → Marketplace.** Add an agent source — the
   [`decentai-agents`](https://github.com/decentai-io/decentai-agents) repository by its address, or your own — read what
   an agent declares, and approve it.
3. **AI → Chats → New chat**, and ask.

## Every day

The app's window says whether DecentAI is running, and has:

| | Does |
|---|---|
| **Open DecentAI** | shows it in a window of its own |
| **Stop**, **Start** | stops it — nothing is removed — and starts it again |
| **Update** | appears when a newer release exists: a copy of the database is kept first, and the version you have is put back if the new one does not start |
| **Check for updates** | looks again |
| **Where my data is** | the places on this computer DecentAI keeps things in |
| **Uninstall** | removes DecentAI and everything it kept, offering a copy of the database in your Documents folder first |

Closing the window leaves an icon by the clock (the menu bar on a Mac),
with Open, Start and Stop in its menu. DecentAI itself keeps running
whether the app is open or not.

## From a command line

The app is the way in for a person; the same things, and a few more,
can be asked for from a terminal with the starter scripts in the
repository (`launcher/starter/`). On Windows `DecentAI.cmd …`; on a Mac
`./DecentAI.command …`:

| Command | Does |
|---|---|
| (nothing) | starts DecentAI if it is stopped, and opens it |
| `status` | what is installed and what is running |
| `stop` | stops it; nothing is removed |
| `update` | installs a newer release, after asking |
| `backup` | a copy of the database, now |
| `stop-everything` | ends everything the agents are doing, when a chat's own Stop is not enough |
| `reset-password` | a new password, when you have forgotten yours: it asks whose (the first person's, unless you say) and the new one twice |
| `engine` | which engine the install is on, and why |
| `develop <folder>` | lets the git repositories in a folder of yours be agent sources, as `/develop` or `/develop/<its folder>`; `develop off` takes it back |

An install made with a script is found by the app, and the other way
round: both keep the same record of it.

`DECENTAI_PORT` chooses another port for a first install, when 4280 is
taken. `DECENTAI_DNS` says where DecentAI looks names up: addresses of
your own DNS servers, when a model or a system of yours has a name only
they know, or `host` for the engine's own.

## Writing agents here

Hand your agents folder over with `DecentAI.cmd develop
C:\Users\you\agents` (on a Mac, `./DecentAI.command develop
~/agents`, a folder in your home folder), and add `/develop/<repository>` as a source on
the marketplace. A source is read at a commit: change the agent,
commit, press **Refresh** on the source, and take the **Update** the
agent offers. The agent template's developing guide has the rest.

## A build of your own

To run what is in a checkout of the repository rather than a release,
build the four images, with Docker or Podman already running (about
five minutes, longer the first time on a Mac):

```bash
docker build -t decentai-backend:local -f backend/Dockerfile .
docker build -t decentai-ai-runtime:local -f ai_runtime/Dockerfile .
docker build -t decentai-caddy:local -f frontend/Dockerfile .
docker build -t decentai-launcher:local -f launcher/Dockerfile .
```

(With Podman, `podman build` the same four.) A build made on the
machine carries no signature, so say so, and start with the starter in
the checkout. On Windows:

```bat
set DECENTAI_UNSIGNED=1
launcher\starter\windows\DecentAI.cmd
```

On a Mac, in Terminal:

```bash
DECENTAI_UNSIGNED=1 ./launcher/starter/macos/DecentAI.command
```

## If something does not start

What the app asked for and what it was answered is kept in `app.log`,
beside what it remembers (**Where my data is** says where). It is what
to send when asking for help.


- **The app says the engine is not running.** **Start** it from the
  app, or open Docker Desktop (or run `podman machine start`) and look
  again. An install
  made on one engine is never started on the other.
- **`status` shows a service that is not healthy.** `update --again`
  reinstalls the same version with a fresh copy of the database kept.
- **A chat says the language model is unavailable.** The sentence
  says why, in the provider's own words: a refused key, a model name it
  does not know, an account out of credit, or an address that could not
  be reached.
- **The page does not open.** Another program may hold port 4280.
- **You forgot your password.** This DecentAI sends no email, so
  "Forgot password" cannot send a link. Run the starter with
  `reset-password` on this computer; it ends every session of the account and lifts the
  lockout after wrong passwords.
