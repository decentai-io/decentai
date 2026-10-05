"""DecentAI on this computer, from a clone, in one command.

    python bootstrap/setup.py            set it up (the first time), start it,
                                         and say where it is
    python bootstrap/setup.py --link     the address that opens it signed in
    python bootstrap/setup.py --port N   the port, said the first time

It needs Docker with Compose and this Python, and imports nothing but
the standard library.

The first time, it writes `deploy.env` from `deploy.env.example`: the
keys, made by the platform's own generators inside the backend's
image; a password for the database; and a first person nobody had to
sign up as — an address that is nobody's and a password nobody chose.
Then it starts the stack and prints an address that opens DecentAI
signed in as that person.

The platform's sign-in is unchanged and is what is used: the address
carries the email and the password in its fragment, which a browser
sends nowhere, and the sign-in page takes them out of the address and
signs in with them as it would with ones typed. Both stay in
`deploy.env`, on this computer. DecentAI is served to this computer
only (docker-compose.yml), so nobody else reaches the page at all.

Run again, it starts what is there and prints the address: the keys
are made once, because a database is readable with the keys it was
written with and no others. A `deploy.env` written by hand for a
server is left alone (docs/run/deploying.md says how that one is
started).
"""

from __future__ import annotations

import argparse
import base64
import json
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

    def _run(self, argv: Sequence[str], shown: bool) -> Tuple[int, str]:
        argv = [self.COMMAND, *argv]
        if Engine.runner is not None:
            code, said = Engine.runner(list(argv), shown)
            return code, str(said or "")
        try:
            done = subprocess.run(
                argv, cwd=self.folder,
                stdout=None if shown else subprocess.PIPE,
                stderr=None if shown else subprocess.STDOUT)
        except OSError as failed:
            raise SetupError(
                f"Docker could not be run ({failed}). Is it installed, and "
                f"running?") from failed
        said = "" if shown else done.stdout.decode("utf-8", "replace")
        return done.returncode, said


class Settings:
    """`deploy.env`: the one file the stack is configured by."""

    TEMPLATE = "deploy.env.example"
    FILE = "deploy.env"
    DEFAULT_PORT = 4280
    #: The address the first person is made with: nobody's.
    FIRST_PERSON = "me@decentai.local"

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.path = self.folder / self.FILE

    @property
    def written(self) -> bool:
        return self.path.is_file()

    def values(self) -> Dict[str, str]:
        """What the file says, name by name."""
        found: Dict[str, str] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            name, mark, value = line.partition("=")
            if mark and name.strip() and not name.lstrip().startswith("#"):
                found[name.strip()] = value.strip()
        return found

    @staticmethod
    def a_password() -> str:
        """A password nobody chose. It begins with a letter and a
        number because the platform asks a password for both, whatever
        the rest came out as."""
        return "d4" + secrets.token_urlsafe(24)

    def local(self, port: int, keys: Dict[str, str]) -> Dict[str, str]:
        """What a run on one person's own computer sets: the template's
        settings that cannot be left as they are written there."""
        address = f"http://localhost:{int(port)}"
        database = secrets.token_urlsafe(32)
        return {
            "PORT": str(int(port)),
            "PUBLIC_APP_URL": address,
            "CORS_ALLOW_ORIGINS": address,
            "MONGO_ROOT_PASSWORD": database,
            "MONGO_URI": (f"mongodb://decentai:{database}"
                          f"@mongo:27017/?authSource=admin"),
            "ADMIN_NAME": "Me",
            "ADMIN_EMAIL": self.FIRST_PERSON,
            "ADMIN_PASSWORD": self.a_password(),
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
        staging = self.folder / f".{self.FILE}.incoming"
        staging.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        try:
            os.chmod(staging, 0o600)
        except OSError:
            pass
        staging.replace(self.path)


class Setup:
    #: The platform's generators, as the backend's image holds them.
    GENERATORS = "/opt/decentai/bootstrap"
    KEYS = {
        "generate_service_keys.py": ("BACKEND_SERVICE_PRIVATE_KEY",
                                     "BACKEND_SERVICE_PUBLIC_KEY"),
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

    # ------------------------------------------------------------------
    def run(self, port: Optional[int] = None) -> str:
        """Set up if nothing is, start, and return the address that
        opens DecentAI signed in."""
        self._must_reach_docker()
        if self.settings.written:
            if port is not None:
                raise SetupError(
                    f"{Settings.FILE} is already here and says the port: "
                    f"change PORT, PUBLIC_APP_URL and CORS_ALLOW_ORIGINS "
                    f"there together.")
            self._must_be_local()
        else:
            self.say("Building the backend, to make this install's keys...")
            self.settings.write(self.settings.local(
                port or Settings.DEFAULT_PORT, self._keys()))
            self.say(f"{Settings.FILE} is written: the keys, the database's "
                     f"password and a first person. Keep it; it is not made "
                     f"twice.")
        self.say("Starting DecentAI (the first build takes a few minutes)...")
        if self.engine.show(self._compose("up", "-d", "--build")) != 0:
            raise SetupError(
                "The stack did not start. What Docker said is above; "
                f"`docker compose --env-file {Settings.FILE} logs` says more.")
        self._wait_until_it_answers()
        return self.link()

    def link(self) -> str:
        """The address that opens DecentAI signed in as the first
        person: the sign-in, in the part of an address that is sent
        nowhere, as the sign-in page reads it."""
        if not self.settings.written:
            raise SetupError(
                f"Nothing is set up here yet: `python bootstrap/setup.py` "
                f"does it.")
        self._must_be_local()
        said = self.settings.values()
        handed = json.dumps({"email": said.get("ADMIN_EMAIL", ""),
                             "password": said.get("ADMIN_PASSWORD", "")})
        return (self._address() + "/#enter="
                + base64.urlsafe_b64encode(handed.encode()).decode().rstrip("="))

    # ------------------------------------------------------------------
    def _compose(self, *arguments: str) -> List[str]:
        return ["compose", "--env-file", Settings.FILE, *arguments]

    def _address(self) -> str:
        return self.settings.values().get("PUBLIC_APP_URL", "").rstrip("/")

    def _keys(self) -> Dict[str, str]:
        """The keys, made by the platform's own generators in the
        backend's image: this computer needs nothing installed for
        them, and the image is the one the stack is about to run."""
        code, said = self.engine.ask(
            ["build", "-q", "-f", "backend/Dockerfile", "."])
        image = said.strip().splitlines()[-1].strip() if said.strip() else ""
        if code != 0 or not image:
            raise SetupError(
                "The backend's image could not be built:\n" + said.strip()[-800:])
        keys: Dict[str, str] = {}
        for script, names in self.KEYS.items():
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

    def _must_be_local(self) -> None:
        address = self._address()
        if not address.startswith(("http://localhost:", "http://127.0.0.1:")):
            raise SetupError(
                f"{Settings.FILE} here is a server's (PUBLIC_APP_URL="
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
                        help="print the address that opens it signed in, "
                             "and start nothing")
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
    print("\nDecentAI is running. Open this address; it signs you in:\n")
    print(f"  {link}\n")
    print(f"`python bootstrap/setup.py --link` says it again. To stop: "
          f"`docker compose --env-file {Settings.FILE} stop`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
