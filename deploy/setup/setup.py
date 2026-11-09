"""DecentAI on this computer, from a clone, in one command. It needs Docker with Compose and this Python, and imports
nothing but the standard library.

    python deploy/setup/setup.py            set it up the first time, start it, and say where it is
    python deploy/setup/setup.py --port N   the port, said the first time
    python deploy/setup/setup.py --link     the address and the first run's code again, starting nothing

The first run writes deploy/config/deploy.env from its template, with its passwords, its key and the first run's
code; they are made once, since a database is readable with no other key. This computer also keeps the deployment's
root, in deploy/config/root, and signs the issuer's intermediate with it. A file written by hand for a server is left
alone.
"""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


class SetupError(RuntimeError):
    """What went wrong, for the person at the keyboard."""


class Docker:
    """Docker's own command line, from the repository's root, with Compose reading the install's file."""

    FOLDER = Path(__file__).resolve().parents[2]

    @classmethod
    def run(cls, *argv: str, shown: bool = False) -> tuple[int, str]:
        """One command; what it prints left on the screen when shown, since a build is long and silence reads as a
        fault."""
        environment = {k: v for k, v in os.environ.items() if k.upper() not in ("COMPOSE_FILE", "COMPOSE_PATH_SEPARATOR")}
        try:
            done = subprocess.run(["docker", *argv], cwd=cls.FOLDER, env=environment,
                                  stdout=None if shown else subprocess.PIPE, stderr=None if shown else subprocess.STDOUT)
        except OSError as failed:
            raise SetupError(f"Docker could not be run ({failed}). Is it installed, and running?") from failed
        return done.returncode, "" if shown else done.stdout.decode("utf-8", "replace")

    @classmethod
    def compose(cls, *argv: str, shown: bool = False) -> int:
        return cls.run("compose", "--env-file", Settings.FILE, *argv, shown=shown)[0]


class Settings:
    """deploy/config/deploy.env: the one file the stack is configured by."""

    TEMPLATE, FILE = "deploy/config/deploy.env.example", "deploy/config/deploy.env"
    DEFAULT_PORT, WEB_CLIENT_PORT, WEB_CLIENT_DEV_PORT = 4280, 4300, 4200

    def __init__(self):
        self.path = Docker.FOLDER / self.FILE

    def values(self) -> dict[str, str]:
        found = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            name, mark, value = line.partition("=")
            if mark and name.strip() and not name.lstrip().startswith("#"):
                found[name.strip()] = value.strip()
        return found

    def write(self, port: int) -> None:
        """The template, its change-me values made here."""
        made = {
            "PORT": str(port), "AI_API_URL": f"http://localhost:{port}",
            "AI_API_APP_URL": f"http://localhost:{self.WEB_CLIENT_PORT}",
            "AI_API_ORIGINS": f"http://localhost:{self.WEB_CLIENT_PORT},http://localhost:{self.WEB_CLIENT_DEV_PORT}",
            "MONGO_ROOT_PASSWORD": secrets.token_urlsafe(32), "ISSUER_DATABASE_PASSWORD": secrets.token_urlsafe(32),
            "AI_API_DATABASE_URI": f"mongodb://api:{secrets.token_urlsafe(32)}@mongo:27017/?replicaSet=rs0"
                                   f"&authSource=decentai",
            "AI_API_ENCRYPTION_KEYS": f"01:{base64.b64encode(secrets.token_bytes(32)).decode()}",
            "AI_API_SETUP_CODE": secrets.token_hex(6)}
        lines = ["# Written by deploy/setup/setup.py for this computer, every generated value in place. It is the "
                 "install:", "# its keys are made once. What follows is the template, which explains each.", "#"]
        for line in (Docker.FOLDER / self.TEMPLATE).read_text(encoding="utf-8").splitlines():
            name = line.partition("=")[0].strip()
            lines.append(f"{name}={made.pop(name)}" if "=" in line and name in made else line)
        if made:
            raise SetupError(f"{self.TEMPLATE} names no {', '.join(sorted(made))}: it isn't the template this script "
                             f"was written for.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_name(f".{self.path.name}.incoming")
        staging.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        try:
            os.chmod(staging, 0o600)
        except OSError:
            pass
        staging.replace(self.path)


class Root:
    """The root's part, done here, since this computer holds the root: on a server it stays off the machine."""

    FOLDER, NAME, KINDS, DAYS = "deploy/config/root", "decentai-local-issuer-1-ca", "issuer,api,runtime,sandbox", 3650
    AUTHORITY = "/run/decentai/authority"

    def make(self) -> None:
        """The two roots, once. Nobody is here to type it, so the passphrase is kept beside the keys."""
        folder = Docker.FOLDER / self.FOLDER
        if (folder / "roots.pem").is_file():
            return
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        (folder / "passphrase").write_text(secrets.token_urlsafe(32), encoding="utf-8")
        if Docker.compose("run", "--rm", "--build", "root", "init", "local", shown=True) != 0:
            raise SetupError("The deployment's roots could not be made: what Docker said is above.")

    def sign(self) -> None:
        """The issuer's request signed and put back, the first time: it certifies nobody until then."""
        if Docker.compose("exec", "-T", "issuer", "test", "-f", f"{self.AUTHORITY}/authority.pem") == 0:
            return
        deadline = time.monotonic() + Setup.WAIT_SECONDS
        while Docker.compose("cp", f"issuer:{self.AUTHORITY}/authority.csr", f"{self.FOLDER}/authority.csr") != 0:
            if time.monotonic() > deadline:
                raise SetupError(f"The issuer made no request in time: `docker compose --env-file {Settings.FILE} "
                                 f"logs issuer` says why.")
            time.sleep(3)
        (Docker.FOLDER / self.FOLDER / f"{self.NAME}.pem").unlink(missing_ok=True)
        if Docker.compose("run", "--rm", "root", "sign", "authority.csr", "--name", self.NAME, "--kinds", self.KINDS,
                          "--days", str(self.DAYS), "--yes", shown=True) != 0 \
                or Docker.compose("cp", f"{self.FOLDER}/{self.NAME}.pem", f"issuer:{self.AUTHORITY}/authority.pem") != 0:
            raise SetupError("The issuer's intermediate could not be signed and put back: what Docker said is above.")


class Setup:
    WAIT_SECONDS = 600

    def __init__(self):
        self.settings = Settings()

    def run(self, port: int | None) -> None:
        if Docker.run("info", "--format", "{{.ServerVersion}}")[0] != 0:
            raise SetupError("Docker did not answer. Start Docker Desktop, or the Docker service on Linux, and run "
                             "this again.")
        if self.settings.path.is_file():
            if port is not None:
                raise SetupError(f"{Settings.FILE} is already here and says the port: change PORT and AI_API_URL "
                                 f"there together.")
            self._must_be_this_computers()
        else:
            self.settings.write(port or Settings.DEFAULT_PORT)
            print(f"{Settings.FILE} is written, with its passwords and key. Keep it: they are not made twice.")
        Root().make()
        print("Starting DecentAI (the first build takes a few minutes)...", flush=True)
        if Docker.compose("up", "-d", "--build", "--remove-orphans", shown=True) != 0:
            raise SetupError(f"The stack did not start. What Docker said is above; `docker compose --env-file "
                             f"{Settings.FILE} logs` says more.")
        Root().sign()
        deadline = time.monotonic() + self.WAIT_SECONDS
        while not self._answers(self._address() + "/readyz"):
            if time.monotonic() > deadline:
                raise SetupError(f"DecentAI did not answer at {self._address()} in time. `docker compose --env-file "
                                 f"{Settings.FILE} ps` says which part is not up, and `logs <part>` why.")
            time.sleep(3)

    def link(self) -> str:
        if not self.settings.path.is_file():
            raise SetupError("Nothing is set up here yet: `python deploy/setup/setup.py` does it.")
        self._must_be_this_computers()
        code = self.settings.values().get("AI_API_SETUP_CODE") or "the one in `logs api-server`"
        return f"The API server: {self._address()}\nThe first run's code: {code}"

    def _address(self) -> str:
        return self.settings.values().get("AI_API_URL", "").rstrip("/")

    def _must_be_this_computers(self) -> None:
        said = self.settings.values()
        if "AI_API_URL" not in said:
            raise SetupError(f"{Settings.FILE} is from before the platform's redesign, and its data isn't carried "
                             f"over. Move the file aside, and `docker compose -p decentai down -v`, then run this "
                             f"again.")
        if not said["AI_API_URL"].startswith(("http://localhost:", "http://127.0.0.1:")):
            raise SetupError(f"{Settings.FILE} here is a server's (AI_API_URL={said['AI_API_URL']}), written by hand: "
                             f"start it with `docker compose --env-file {Settings.FILE} up -d`.")

    @staticmethod
    def _answers(address: str) -> bool:
        try:
            with urllib.request.urlopen(address, timeout=5) as answer:
                return answer.status == 200
        except (OSError, urllib.error.URLError):
            return False

    @classmethod
    def main(cls) -> int:
        parser = argparse.ArgumentParser(prog="setup", description="DecentAI on this computer, from a clone.")
        parser.add_argument("--port", type=int, help=f"where it is opened (first run only; {Settings.DEFAULT_PORT})")
        parser.add_argument("--link", action="store_true", help="say the address again, and start nothing")
        asked = parser.parse_args()
        try:
            if not asked.link:
                cls().run(asked.port)
            print(cls().link())
        except SetupError as failed:
            print(f"\n{failed}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\nStopped. Run it again to carry on.", file=sys.stderr)
            return 130
        if not asked.link:
            print(f"\nThe web client is run from its own repository, beside this one:\n\n"
                  f"  git clone https://github.com/decentai-io/decentai-web-client.git ../decentai-web-client\n"
                  f"  cd ../decentai-web-client && docker compose up -d --build\n\n"
                  f"Open http://localhost:{Settings.WEB_CLIENT_PORT}, give it the API server's address, and the first "
                  f"run's code to name your organization and yourself.")
        return 0


if __name__ == "__main__":
    sys.exit(Setup.main())
