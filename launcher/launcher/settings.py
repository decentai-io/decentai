"""What the launcher keeps: the settings it made, and what is installed.

    <state>/backend.env     the backend's and the seeder's settings
    <state>/runtime.env     the runtime's, and nothing of the backend's
    <state>/mongo.env       the database's root account
    <state>/install.json    which release is installed, and the one before
    <state>/backups/        the database, before each update
    <state>/develop.yml     while somebody develops agents: their folder,
                            handed to the backend to read

The keys are made once, at first run, and never again: the database's
encrypted values are readable with these keys and no others. Nothing
here is typed by the person, and nothing here is shown to them.

What an install IS may be said later. A launcher knows more than the
one that made an install did, and says what that one could not:
``bring_up_to_date`` adds the lines an install's settings lack, and
changes none that are there.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional


class SettingsError(RuntimeError):
    pass


class Settings:
    BACKEND = "backend.env"
    RUNTIME = "runtime.env"
    MONGO = "mongo.env"
    INSTALL = "install.json"
    BACKUPS = "backups"
    DEVELOP = "develop.yml"
    #: Where the backend reads the folder somebody develops agents in.
    DEVELOP_TARGET = "/develop"

    DEFAULT_PORT = 4280
    #: What the engine knows the install by, and names its volumes
    #: after. Not `decentai`: that is what a developer's own stack is
    #: called, and an install must never open another stack's database.
    PROJECT = "decentai-app"
    DATABASE_USER = "decentai"
    DATABASE_NAME = "decentai"

    #: What every install the launcher makes is, said to the backend:
    #: the platform on one person's own computer.
    BACKEND_FACTS = {"DEPLOYMENT_KIND": "desktop"}

    def __init__(self, state: str | Path):
        self.state = Path(state)

    # ------------------------------------------------------------------
    # What is installed
    # ------------------------------------------------------------------

    @property
    def installed(self) -> bool:
        return (self.state / self.INSTALL).is_file()

    def install(self) -> Dict[str, Any]:
        """What is installed, as it was last written; {} before the
        first run."""
        try:
            found = json.loads((self.state / self.INSTALL).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def record(self, **changes: Any) -> Dict[str, Any]:
        """Write what changed about the install, whole and at once: a
        half-written record of what is installed is worse than none."""
        install = {**self.install(), **changes}
        self._write(self.INSTALL, json.dumps(install, indent=1, sort_keys=True))
        return install

    @property
    def port(self) -> int:
        return int(self.install().get("port") or self.DEFAULT_PORT)

    @property
    def address(self) -> str:
        return f"http://localhost:{self.port}"

    # ------------------------------------------------------------------
    # A folder somebody develops agents in
    # ------------------------------------------------------------------

    @property
    def develop_file(self) -> Path:
        return self.state / self.DEVELOP

    @property
    def develop_folder(self) -> str:
        return str(self.install().get("develop_folder") or "")

    def develop(self, folder: str) -> None:
        """Hand ``folder`` to the backend, read-only, as the one place
        its agent sources may come from on this computer — or, given
        '', take it back. ``folder`` is where the ENGINE finds it: the
        starter, which knows the engine, says it that way."""
        folder = str(folder or "").strip()
        if not folder:
            if self.develop_file.exists():
                self.develop_file.unlink()
            self.record(develop_folder="")
            return
        if not folder.startswith("/") or "\n" in folder or "\r" in folder:
            raise SettingsError(
                f"'{folder}' is not a folder the engine can find: it is "
                f"written as the engine sees it, beginning with /.")
        self._write(self.DEVELOP, "\n".join([
            "# A folder somebody develops agents in, handed to the backend",
            "# to read (docs/system/desktop-install.md). Written by the",
            "# launcher's `develop`, and removed by `develop --off`.",
            "services:",
            "  backend:",
            "    environment:",
            f"      AGENT_SOURCE_FOLDER: {self.DEVELOP_TARGET}",
            "    volumes:",
            "      - type: bind",
            f"        source: {json.dumps(folder)}",
            f"        target: {self.DEVELOP_TARGET}",
            "        read_only: true",
            "",
        ]))
        self.record(develop_folder=folder)

    @property
    def backups(self) -> Path:
        folder = self.state / self.BACKUPS
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # ------------------------------------------------------------------
    # The settings of the three containers
    # ------------------------------------------------------------------

    def write(self, port: int, organization: str, token_key: str,
              service_private_key: str, service_public_key: str,
              encryption_keys: str, encryption_active: str) -> None:
        """The settings of a first run. Refused once they exist: keys
        made twice are a database nobody can read."""
        if (self.state / self.BACKEND).exists():
            raise SettingsError(
                "This install already has its settings, and its keys with "
                "them. They are made once.")
        password = secrets.token_urlsafe(32)
        address = f"http://localhost:{int(port)}"

        self._write(self.MONGO, self._lines({
            "MONGO_INITDB_ROOT_USERNAME": self.DATABASE_USER,
            "MONGO_INITDB_ROOT_PASSWORD": password,
        }))
        self._write(self.BACKEND, self._lines({
            "PUBLIC_APP_URL": address,
            "CORS_ALLOW_ORIGINS": address,
            "MONGO_URI": (f"mongodb://{self.DATABASE_USER}:{password}"
                          f"@mongo:27017/?authSource=admin"),
            "MONGO_DATABASE_NAME": self.DATABASE_NAME,
            "AI_RUNTIME_URL": "http://ai-runtime:8001",
            # Caddy is the only hop in front of the backend, on the
            # stack's own network.
            "FORWARDED_ALLOW_IPS": "*",
            "ORG_NAME": organization,
            "TOKEN_SECRET_KEY": token_key,
            # Plain HTTP, and only from this machine: a cookie marked
            # for HTTPS alone would never be sent back.
            "JWT_COOKIE_SECURE": "false",
            "JWT_COOKIE_SAMESITE": "lax",
            "BACKEND_SERVICE_PRIVATE_KEY": service_private_key,
            "SECRET_ENCRYPTION_KEYS": encryption_keys,
            "SECRET_ENCRYPTION_ACTIVE": encryption_active,
            "FILE_STORAGE_PROVIDER": "local",
            **self.BACKEND_FACTS,
        }))
        # Agent code runs in the runtime's container: it is handed what
        # the runtime reads, and nothing of the backend's.
        self._write(self.RUNTIME, self._lines({
            "BACKEND_SERVICE_PUBLIC_KEY": service_public_key,
            "BACKEND_INTERNAL_URL": "http://backend:8000",
            "AI_RUNTIME_AGENTS_INSTALL_DIR": "/data/agents",
            "AI_RUNTIME_EGRESS_PORT": "8002",
        }))

    def bring_up_to_date(self) -> list:
        """Say to an install what it is, where the launcher that made
        it did not know to. Returns the names that were added. A line
        that is there is left as it is, whatever it says: somebody set
        it."""
        path = self.state / self.BACKEND
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8")
        said = {line.split("=", 1)[0].strip() for line in text.splitlines()
                if "=" in line and not line.lstrip().startswith("#")}
        missing = {name: value for name, value in self.BACKEND_FACTS.items()
                   if name not in said}
        if missing:
            if text and not text.endswith("\n"):
                text += "\n"
            self._write(self.BACKEND, text + self._lines(missing))
        return sorted(missing)

    def stack_environment(self, images: Dict[str, str],
                          port: Optional[int] = None) -> Dict[str, str]:
        """What the compose file is filled in with."""
        return {
            "BACKEND_IMAGE": images["backend"],
            "RUNTIME_IMAGE": images["runtime"],
            "FRONTEND_IMAGE": images["frontend"],
            "PORT": str(port or self.port),
            "STATE": str(self.state),
            "PROJECT": os.environ.get("DECENTAI_PROJECT") or self.PROJECT,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _lines(values: Dict[str, str]) -> str:
        for name, value in values.items():
            if "\n" in str(value) or "\r" in str(value):
                raise SettingsError(f"{name} does not fit on one line")
        return "".join(f"{name}={value}\n" for name, value in values.items())

    def _write(self, name: str, text: str) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        target = self.state / name
        staging = self.state / f".{name}.incoming"
        staging.write_text(text, encoding="utf-8", newline="\n")
        try:
            os.chmod(staging, 0o600)
        except OSError:
            pass
        staging.replace(target)
