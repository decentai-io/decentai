"""The DecentAI engine on this computer, from a clone, in one command.

    python bootstrap/setup.py            set it up (the first time), start it,
                                         and say where it is
    python bootstrap/setup.py --link     the address again, starting nothing
    python bootstrap/setup.py --port N   the port, said the first time

It needs Docker with Compose and this Python, and imports nothing but
the standard library.

Its files are in `config/`. The first time, it writes
`config/deploy.env` from `config/deploy.env.example`: the keys, made
by the platform's own generators inside the Data Core's image, and a
password for the database. Then it starts the stack and prints the
engine's address. The engine is headless — an API, with no page of its
own. The app is the web client, a repository of its own
(decentai-web-client), started beside this one; its first screen asks
for the engine's address, which is what this script prints. Nobody is
made for you: the web client then opens on the first-run page, where
you name your organization and choose your own email and password,
once. After that it is the sign-in page.

The first-run page asks this install for no setup code
(`SETUP_CODE=none`): DecentAI is served to this computer only
(docker-compose.yml), so nobody else reaches the page at all.

Run again, it starts what is there and prints the address: the keys
are made once, because a database is readable with the keys it was
written with and no others. What the platform gained since the file
was written — a setting `config/deploy.env.example` names and the file
does not — is added to the file first: a key, made now by the
generator; anything else, as the template says it. Nothing the file
says is changed. A `config/deploy.env` written by hand for a server is
left alone (docs/run/deploying.md says how that one is started, and
updated).

What the stack is made of is a setting of the file too: COMPOSE_FILE,
docker-compose.yml and whatever is laid over it, such as
compose/sandboxes.yml for a second Execution Sandbox. Compose reads it
from the file itself, so this script starts what the file names and
removes a container of a file it no longer names — and so does every
`docker compose --env-file config/deploy.env` command, with no `-f`.

Two secrets are not in `config/deploy.env`, because Compose gives that
file whole to the Data Core and the seeder, which need neither: the
execution node's secret, in `config/sandbox.env`, given to the AI
Runtime and its Execution Sandboxes alone; and the install's
authority's private key, in `config/node-authority.env`, given to
node-issuer alone. They are written beside it, made once, as the keys
are. A `deploy.env` from before that holds either has it moved out into
its file, and the line it was on says where it went.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, ClassVar, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent.parent
#: The folder, under the repository's root, that holds the install's
#: settings and secrets beside their templates.
CONFIG = "config"


class SetupError(RuntimeError):
    """What went wrong, for the person at the keyboard."""


class Engine:
    """Docker, as this script speaks to it: its own command line."""

    COMMAND = "docker"

    #: Test seam — a callable (argv, shown) returning (code, stdout).
    #: Set it and no command runs.
    runner: ClassVar[Optional[Callable[..., tuple]]] = None

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def ask(self, argv: Sequence[str]) -> Tuple[int, str]:
        """One command, what it printed kept for the script to read."""
        return self._run(argv, shown=False)

    def show(self, argv: Sequence[str]) -> int:
        """One command, what it prints left on the person's screen: a
        build is long, and a wait with no words reads as a fault."""
        return self._run(argv, shown=True)[0]

    #: What Compose would take from this shell before the install's
    #: file: which Compose files the stack is. The file says it.
    FILE_SETTINGS = ("COMPOSE_FILE", "COMPOSE_PATH_SEPARATOR")

    @classmethod
    def environment(cls) -> Dict[str, str]:
        """This shell's environment, but for what would override what
        config/deploy.env says the stack is made of."""
        return {name: value for name, value in os.environ.items()
                if name.upper() not in cls.FILE_SETTINGS}

    def _run(self, argv: Sequence[str], shown: bool) -> Tuple[int, str]:
        argv = [self.COMMAND, *argv]
        if Engine.runner is not None:
            code, said = Engine.runner(list(argv), shown)
            return code, str(said or "")
        try:
            done = subprocess.run(
                argv, cwd=self.folder, env=self.environment(),
                stdout=None if shown else subprocess.PIPE,
                stderr=None if shown else subprocess.STDOUT)
        except OSError as failed:
            raise SetupError(
                f"Docker could not be run ({failed}). Is it installed, and "
                f"running?") from failed
        said = "" if shown else done.stdout.decode("utf-8", "replace")
        return done.returncode, said


class Settings:
    """`config/deploy.env`: the one file the stack is configured by."""

    TEMPLATE = f"{CONFIG}/deploy.env.example"
    FILE = f"{CONFIG}/deploy.env"
    DEFAULT_PORT = 4280
    #: Where the web client is served on this computer: its container
    #: (decentai-web-client's docker compose) and its development
    #: server (npm start). The engine answers the browser from both.
    WEB_CLIENT_PORT = 4300
    WEB_CLIENT_DEV_PORT = 4200

    @classmethod
    def engine_address(cls, port: int) -> str:
        return f"http://localhost:{int(port)}"

    @classmethod
    def web_client_address(cls) -> str:
        return f"http://localhost:{cls.WEB_CLIENT_PORT}"

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.path = self.folder / self.FILE

    @property
    def written(self) -> bool:
        return self.path.is_file()

    def values(self) -> Dict[str, str]:
        """What the file says, name by name."""
        return self._read(self.path)

    def template(self) -> Dict[str, str]:
        """What the template says, name by name, in its order."""
        return self._read(self.folder / self.TEMPLATE)

    def missing(self) -> List[str]:
        """The template's settings the file does not name: what the
        platform gained since the file was written."""
        said = self.values()
        return [name for name in self.template() if name not in said]

    @staticmethod
    def _read(path: Path) -> Dict[str, str]:
        found: Dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            name, mark, value = line.partition("=")
            if mark and name.strip() and not name.lstrip().startswith("#"):
                found[name.strip()] = value.strip()
        return found

    def local(self, port: int, keys: Dict[str, str]) -> Dict[str, str]:
        """What a run on one person's own computer sets: the template's
        settings that cannot be left as they are written there. The
        engine's address carries the port; the web client's addresses
        are where its container and its development server serve it on
        this computer. Nobody is named: the person at the keyboard
        names themselves, on the first-run page, which asks this
        computer for no code."""
        database = secrets.token_urlsafe(32)
        return {
            "PORT": str(int(port)),
            "PUBLIC_ENGINE_URL": self.engine_address(port),
            "PUBLIC_APP_URL": self.web_client_address(),
            "CORS_ALLOW_ORIGINS": ",".join([
                self.web_client_address(),
                f"http://localhost:{self.WEB_CLIENT_DEV_PORT}"]),
            "MONGO_ROOT_PASSWORD": database,
            "MONGO_URI": (f"mongodb://decentai:{database}"
                          f"@mongo:27017/?authSource=admin"),
            "SETUP_CODE": "none",
            "TOKEN_SECRET_KEY": secrets.token_urlsafe(48),
            **keys,
        }

    def write(self, changed: Dict[str, str]) -> None:
        """The template, with ``changed`` in place of what it says for
        those names. Refused once the file exists: keys made twice are
        a database nobody can read."""
        if self.written:
            raise SetupError(
                f"{self.FILE} is already here, and its keys with it. They "
                f"are made once.")
        for name, value in changed.items():
            if "\n" in value or "\r" in value:
                raise SetupError(f"{name} does not fit on one line.")
        left = dict(changed)
        lines: List[str] = [
            "# Written by bootstrap/setup.py for this computer: every value",
            "# it had to generate is in place. It is the install — its keys",
            "# are made once. What follows is the template, as it explains",
            "# each setting.",
            "#"]
        template = (self.folder / self.TEMPLATE).read_text(encoding="utf-8")
        for line in template.splitlines():
            name, mark, _ = line.partition("=")
            if mark and name.strip() in left and not name.lstrip().startswith("#"):
                line = f"{name.strip()}={left.pop(name.strip())}"
            lines.append(line)
        if left:
            raise SetupError(
                f"{self.TEMPLATE} names no {', '.join(sorted(left))}: it is "
                f"not the template this script was written for.")
        self._put("\n".join(lines) + "\n")

    def add(self, added: Dict[str, str]) -> None:
        """``added`` at the end of the file, under a line saying when
        and why. Nothing the file already says is touched: that is what
        keeps the keys made once."""
        said = self.values()
        for name, value in added.items():
            if name in said:
                raise SetupError(f"{self.FILE} already says {name}.")
            if "\n" in value or "\r" in value:
                raise SetupError(f"{name} does not fit on one line.")
        text = self.path.read_text(encoding="utf-8")
        lines = [
            "",
            f"# Added by bootstrap/setup.py on {time.strftime('%Y-%m-%d')}: what "
            "the platform gained",
            f"# since this file was written. {self.TEMPLATE} explains each.",
            *(f"{name}={value}" for name, value in added.items()),
        ]
        self._put(text.rstrip("\n") + "\n" + "\n".join(lines) + "\n")

    def move_out(self, moved: Dict[str, str]) -> None:
        """Each name in ``moved`` taken out of the file, its line
        replaced by one saying which file holds it now (``moved`` maps
        the name to that file). Nothing else the file says changes."""
        kept: List[str] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            name, mark, _ = line.partition("=")
            name = name.strip()
            if mark and name in moved and not line.lstrip().startswith("#"):
                kept.append(f"# {name} moved to {moved[name]} by "
                            f"bootstrap/setup.py on {time.strftime('%Y-%m-%d')}: "
                            f"it is given only to what uses it.")
                continue
            kept.append(line)
        self._put("\n".join(kept) + "\n")

    def _put(self, text: str) -> None:
        """The whole file, in one move: nobody reads it half written."""
        _put(self.path, text)


def _put(path: Path, text: str) -> None:
    """A file holding secrets, in one move and readable by its owner
    only: nobody reads it half written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.incoming"
    staging.write_text(text, encoding="utf-8", newline="\n")
    try:
        os.chmod(staging, 0o600)
    except OSError:
        pass
    staging.replace(path)


class SecretFile:
    """A file of secrets Compose gives to some services only
    (docker-compose.yml, ``env_file``): never to the Data Core.
    ``name`` is its path under the repository's root."""

    def __init__(self, folder: Path, name: str, why: str):
        self.folder = Path(folder)
        self.name = name
        self.path = self.folder / name
        self.why = why

    @property
    def written(self) -> bool:
        return self.path.is_file()

    def values(self) -> Dict[str, str]:
        return Settings._read(self.path) if self.written else {}

    def write(self, values: Dict[str, str]) -> None:
        for name, value in values.items():
            if not value or "\n" in value or "\r" in value:
                raise SetupError(f"{name} does not fit on one line.")
        _put(self.path, "\n".join([
            f"# Written by bootstrap/setup.py: {self.why}",
            f"# Made once, like the keys in {Settings.FILE}; keep it.",
            *(f"{name}={value}" for name, value in values.items()),
        ]) + "\n")


class Setup:
    #: The platform's generators, as the Data Core's image holds them.
    GENERATORS = "/opt/decentai/bootstrap"
    KEYS = {
        "generate_service_keys.py": ("DATA_CORE_SERVICE_PRIVATE_KEY",
                                     "DATA_CORE_SERVICE_PUBLIC_KEY"),
        # The install's own node authority: registered as a shared
        # authority by the seeder, and the bundled node's credentials
        # signed with it by node-issuer — nothing asked of the person.
        "generate_node_authority.py": ("NODE_AUTHORITY_PRIVATE_KEY",
                                       "NODE_AUTHORITY_PUBLIC_KEY"),
        "generate_secret_keys.py": ("SECRET_ENCRYPTION_KEYS",
                                    "SECRET_ENCRYPTION_ACTIVE"),
    }
    UP_SECONDS = 300

    def __init__(self, folder: Path = HERE,
                 say: Optional[Callable[[str], None]] = None):
        self.folder = Path(folder)
        self.settings = Settings(self.folder)
        self.engine = Engine(self.folder)
        # Said at once: Docker's own words follow on the same screen,
        # and a line kept back would come out after them.
        self.say = say or (lambda text: print(text, flush=True))
        #: Test seams: how long a wait between two looks lasts, and how
        #: the address is asked whether it answers.
        self.pause = 3.0
        self.answers: Callable[[str], bool] = self._answers

    #: What a setting this computer's file gains is set to, where the
    #: template's word is a server's: the first-run page asks a computer
    #: of one's own for no code.
    LOCAL = {"SETUP_CODE": "none"}
    #: What a setting this computer's file gains is made as, where it
    #: is a secret of the install's own: never the template's word.
    MADE: Dict[str, Callable[[], str]] = {}
    #: The secrets kept out of deploy.env, and the file each is in:
    #: Compose gives each file only to the services that use it.
    SANDBOX_SECRET = "AI_RUNTIME_SANDBOX_SECRET"
    AUTHORITY_KEY = "NODE_AUTHORITY_PRIVATE_KEY"
    SECRET_FILES = {
        SANDBOX_SECRET: (f"{CONFIG}/sandbox.env",
                         "the execution node's secret, which the AI Runtime "
                         "and its Execution Sandboxes prove to each other."),
        AUTHORITY_KEY: (f"{CONFIG}/node-authority.env",
                        "the install's node authority's private key, which "
                        "node-issuer alone signs the bundled node's "
                        "credentials with."),
    }

    # ------------------------------------------------------------------
    def run(self, port: Optional[int] = None) -> str:
        """Set up if nothing is, start, and return DecentAI's address."""
        self._must_reach_docker()
        if self.settings.written:
            if port is not None:
                raise SetupError(
                    f"{Settings.FILE} is already here and says the port: "
                    f"change PORT and PUBLIC_ENGINE_URL there together.")
            self._must_be_local()
            self._secrets(self._complete())
            self._say_where_the_app_now_is()
        else:
            self.say("Building the Data Core's image, to make this "
                     "install's keys...")
            keys = self._keys()
            authority = keys.pop(self.AUTHORITY_KEY)
            self.settings.write(self.settings.local(
                port or Settings.DEFAULT_PORT, keys))
            self._secrets({self.AUTHORITY_KEY: authority})
            self.say(f"{Settings.FILE} is written: the keys and the "
                     f"database's password; and beside it sandbox.env and "
                     f"node-authority.env, each given only to what uses it. "
                     f"Keep the three; they are not made twice.")
        self._must_name_files_here()
        self.say("Starting DecentAI (the first build takes a few minutes)...")
        # --remove-orphans: a container the stack no longer defines (the
        # gateway's, before the engine became a headless API; the web
        # app's, before it became a repository of its own) would keep the
        # port the Data Core is to take, and the Data Core would not start.
        # What it defines is every file COMPOSE_FILE names, which Compose
        # reads from config/deploy.env: a second sandbox named there is
        # the stack's, and stays.
        if self.engine.show(self._compose(
                "up", "-d", "--build", "--remove-orphans")) != 0:
            raise SetupError(
                "The stack did not start. What Docker said is above; "
                f"`docker compose --env-file {Settings.FILE} logs` says more.")
        self._wait_until_it_answers()
        return self.link()

    def link(self) -> str:
        """The engine's address: what the web client's first screen asks
        for. It carries nothing — no password is written for anybody to
        be signed in with."""
        if not self.settings.written:
            raise SetupError(
                "Nothing is set up here yet: `python bootstrap/setup.py` "
                "does it.")
        self._must_be_local()
        return self._address()

    # ------------------------------------------------------------------
    def _compose(self, *arguments: str) -> List[str]:
        return ["compose", "--env-file", Settings.FILE, *arguments]

    def _address(self) -> str:
        """The engine's address. A file from before the engine went
        headless names no PUBLIC_ENGINE_URL, and its PUBLIC_APP_URL was
        the engine's address then."""
        said = self.settings.values()
        return (said.get("PUBLIC_ENGINE_URL") or said.get("PUBLIC_APP_URL", "")).rstrip("/")

    def _say_where_the_app_now_is(self) -> None:
        """A file from before the engine went headless points the app's
        address at the engine, which serves no page any more. Nothing
        the file says is changed; it is said, with what to change it to."""
        said = self.settings.values()
        engine = self._address()
        app = said.get("PUBLIC_APP_URL", "").rstrip("/")
        if app and app == engine:
            self.say(
                f"{Settings.FILE} says the app is at {app}, which is the "
                f"engine's address: the engine serves no page any more. Set "
                f"PUBLIC_APP_URL to where the web client is served "
                f"({Settings.web_client_address()} for its container here) "
                f"and CORS_ALLOW_ORIGINS to every origin it is served from "
                f"({Settings.web_client_address()},http://localhost:"
                f"{Settings.WEB_CLIENT_DEV_PORT} for its container and its "
                f"development server), then run this again.")

    def _complete(self) -> Dict[str, str]:
        """What the template gained since the file was written, added
        to it. A key is made now, by the generator that makes it — and
        only the key the file lacks is taken from what it prints: the
        keys the file holds are kept, as they are made once. Any other
        setting is added as the template says it. Returns what was made,
        so a secret kept outside the file finds its way to its own."""
        missing = self.settings.missing()
        if not missing:
            return {}
        generators = {script for script, names in self.KEYS.items()
                      if set(names) & set(missing)}
        if generators:
            self.say("Building the Data Core's image, to make the keys "
                     "this install lacks...")
        made = self._keys(generators) if generators else {}
        template = self.settings.template()
        made.update({name: make() for name, make in self.MADE.items()
                     if name in missing and name not in made})
        local = dict(self.LOCAL)
        if "PUBLIC_ENGINE_URL" in missing:
            # The engine's own address, new to the file: the one the file
            # already names as the engine's, by its port.
            said = self.settings.values()
            port = said.get("PORT") or str(Settings.DEFAULT_PORT)
            local["PUBLIC_ENGINE_URL"] = (
                Settings.engine_address(int(port)) if port.isdigit()
                else said.get("PUBLIC_APP_URL", template["PUBLIC_ENGINE_URL"]))
        self.settings.add({name: made.get(name, local.get(name, template[name]))
                           for name in missing})
        self.say(f"{Settings.FILE} gained {', '.join(missing)}: what the "
                 f"platform has since the file was written. Nothing else in "
                 f"it changed.")
        return made

    def _secrets(self, made: Dict[str, str]) -> None:
        """The secrets kept out of deploy.env, each in its own file:
        written once, from what this run made, or moved out of a
        deploy.env from before that still holds them. A private key just
        made always replaces its file, since its public half was just
        written beside it."""
        said = self.settings.values()
        moved: Dict[str, str] = {}
        for name, (file_name, why) in self.SECRET_FILES.items():
            secret_file = SecretFile(self.folder, file_name, why)
            value = made.get(name)
            if secret_file.written and not value:
                if name in said:
                    moved[name] = file_name
                continue
            value = value or said.get(name)
            if not value and name == self.SANDBOX_SECRET:
                value = secrets.token_urlsafe(32)
            if not value:
                raise SetupError(
                    f"{file_name} is missing, and nothing here holds {name} "
                    f"to write it from. Restore it from a backup, or make a "
                    f"new pair with bootstrap/generate_node_authority.py and "
                    f"put the public half in {Settings.FILE} as "
                    f"NODE_AUTHORITY_PUBLIC_KEY and the private half in "
                    f"{file_name}.")
            secret_file.write({name: value})
            if name in said:
                moved[name] = file_name
        if moved:
            self.settings.move_out(moved)
            self.say(f"{', '.join(moved)} moved out of {Settings.FILE} into "
                     f"{', '.join(sorted(set(moved.values())))}: the Data Core "
                     f"is given {Settings.FILE} whole, and needs neither.")

    def _keys(self, scripts: Optional[set] = None) -> Dict[str, str]:
        """The keys, made by the platform's own generators in the
        Data Core's image: this computer needs nothing installed for
        them, and the image is the one the stack is about to run.
        ``scripts`` names the generators to run; all of them, unsaid."""
        code, said = self.engine.ask(
            ["build", "-q", "-f", "data_core/Dockerfile", "."])
        image = said.strip().splitlines()[-1].strip() if said.strip() else ""
        if code != 0 or not image:
            raise SetupError(
                "The Data Core's image could not be built:\n" + said.strip()[-800:])
        keys: Dict[str, str] = {}
        for script, names in self.KEYS.items():
            if scripts is not None and script not in scripts:
                continue
            code, said = self.engine.ask(
                ["run", "--rm", image, "python", f"{self.GENERATORS}/{script}"])
            for name in names:
                value = next(
                    (line[len(name) + 1:].strip() for line in said.splitlines()
                     if line.startswith(name + "=")), "")
                if code != 0 or not value:
                    raise SetupError(f"The platform did not make {name}.")
                keys[name] = value
        return keys

    def _wait_until_it_answers(self) -> None:
        address = self._address() + "/healthz"
        deadline = time.monotonic() + self.UP_SECONDS
        while not self.answers(address):
            if time.monotonic() > deadline:
                raise SetupError(
                    f"DecentAI did not answer at {self._address()} in time. "
                    f"`docker compose --env-file {Settings.FILE} ps` says "
                    f"which part is not up, and `logs <part>` why.")
            time.sleep(self.pause)

    @staticmethod
    def _answers(address: str) -> bool:
        try:
            with urllib.request.urlopen(address, timeout=5) as answer:
                return answer.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def _must_reach_docker(self) -> None:
        code, said = self.engine.ask(["compose", "version"])
        if code != 0:
            raise SetupError(
                "Docker with Compose is needed, and did not answer: "
                + (said.strip().splitlines() or ["no answer"])[-1])
        # Compose answers for itself with nothing behind it, so the
        # engine is asked too: a build that finds none says so in
        # Docker's words, which name a pipe and not what to do.
        code, _ = self.engine.ask(["info", "--format", "{{.ServerVersion}}"])
        if code != 0:
            raise SetupError(
                "Docker is installed and is not running. Start Docker "
                "Desktop — or the Docker service, on Linux — and run this "
                "again.")

    def _must_name_files_here(self) -> None:
        """Every Compose file config/deploy.env says the stack is made
        of is here, said before Compose says it in a path's words."""
        said = self.settings.values()
        separator = said.get("COMPOSE_PATH_SEPARATOR") or os.pathsep
        named = [name.strip()
                 for name in said.get("COMPOSE_FILE", "").split(separator)]
        if not any(named):
            raise SetupError(
                f"{Settings.FILE} says COMPOSE_FILE is nothing: it is the "
                f"stack's Compose files, docker-compose.yml and what is laid "
                f"over it, as {Settings.TEMPLATE} says.")
        missing = [name for name in named
                   if not name or not (self.folder / name).is_file()]
        if missing:
            raise SetupError(
                f"{Settings.FILE} says the stack is made of "
                f"{said['COMPOSE_FILE']}, and this is not a file here: "
                f"{', '.join(repr(name) for name in missing)}. Name each "
                f"from the repository's root, separated by '{separator}'.")

    def _must_be_local(self) -> None:
        address = self._address()
        if not address.startswith(("http://localhost:", "http://127.0.0.1:")):
            raise SetupError(
                f"{Settings.FILE} here is a server's (PUBLIC_ENGINE_URL="
                f"{address or 'unset'}), written by hand: it is started as "
                f"docs/run/deploying.md says, and signed in to with the "
                f"administrator it names.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="setup", description="DecentAI on this computer, from a clone.")
    parser.add_argument("--port", type=int,
                        help=f"where it is opened (first run only; "
                             f"{Settings.DEFAULT_PORT} when left out)")
    parser.add_argument("--link", action="store_true",
                        help="print the address, and start nothing")
    asked = parser.parse_args(argv)
    setup = Setup()
    try:
        if asked.link:
            print(setup.link())
            return 0
        link = setup.run(asked.port)
    except SetupError as failed:
        print(f"\n{failed}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Run it again to carry on.", file=sys.stderr)
        return 130
    print("\nThe DecentAI engine's API is running at:\n")
    print(f"  {link}\n")
    print("It is an API alone, with no page of its own. The web client is "
          "run separately, from its own repository: clone it beside this "
          "one and start it there, with `npm start` while developing it "
          f"(http://localhost:{Settings.WEB_CLIENT_DEV_PORT}) or as its "
          f"container ({Settings.web_client_address()}):\n")
    print("  git clone https://github.com/decentai-io/decentai-web-client.git "
          "../decentai-web-client")
    print("  cd ../decentai-web-client && docker compose up -d --build\n")
    print("Its first screen asks for the engine's address: give it the one "
          "above. The first time, it then asks you to name your "
          "organization and choose your own email and password. After "
          "that, sign in with them.")
    print(f"`python bootstrap/setup.py --link` says the engine's address "
          f"again. To stop: `docker compose --env-file {Settings.FILE} stop`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
