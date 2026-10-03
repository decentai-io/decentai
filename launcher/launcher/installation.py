"""One install of the platform on one person's computer: its first run,
its starts and stops, and its updates (docs/system/desktop-install.md).

    installation = Installation(settings, engine, say)

    installation.first_run(release, email, password, name, port)
    installation.start() / stop() / status()
    installation.update(release)
    installation.stop_everything()
    installation.reset_password(email, password)

An update never touches what the person made — the database, the files
they uploaded, the agents they approved, the keys. It takes a copy of
the database first, and when the new release does not come up healthy
it puts the release before it back, and the copy with it.
"""

from __future__ import annotations

import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from launcher.engine import Engine, EngineError
from launcher.release import Release
from launcher.settings import Settings


class InstallationError(RuntimeError):
    """What went wrong, for the person at the keyboard."""


class Installation:
    #: What has to be up for the person to be told it is.
    SERVING = ("backend", "ai-runtime", "caddy")
    HEALTHY_SECONDS = 300
    BACKUPS_KEPT = 3

    EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    #: The platform's own rule for a password, said here so that a
    #: first run is refused before anything is pulled.
    PASSWORD_MIN = 10

    DUMP = ('mongodump --quiet --username "$MONGO_INITDB_ROOT_USERNAME" '
            '--password "$MONGO_INITDB_ROOT_PASSWORD" '
            '--authenticationDatabase admin --db {name} --archive --gzip')
    RESTORE = ('mongorestore --quiet --username "$MONGO_INITDB_ROOT_USERNAME" '
               '--password "$MONGO_INITDB_ROOT_PASSWORD" '
               '--authenticationDatabase admin --drop --archive --gzip')

    def __init__(self, settings: Settings, engine: Engine,
                 say: Callable[[str], None] = print):
        self.settings = settings
        self.engine = engine
        self.say = say
        #: Test seam: how long a wait between two looks lasts.
        self.pause = 3.0

    # ------------------------------------------------------------------
    # The first run
    # ------------------------------------------------------------------

    def first_run(self, release: Release, email: str, password: str,
                  name: str = "", port: Optional[int] = None) -> str:
        """Install ``release`` and the first person. Returns the address
        the person opens."""
        if self.settings.installed:
            raise InstallationError(
                f"DecentAI is already installed here "
                f"(version {self.settings.install().get('version')}). "
                f"`update` installs a newer release; `start` starts this one.")
        problems = self.first_person_problems(email, password)
        if problems:
            raise InstallationError(" ".join(problems))
        port = int(port or Settings.DEFAULT_PORT)

        self._must_reach_the_engine()
        self._pull(release)
        if self.settings.begun:
            # An earlier first run made the keys, perhaps the database
            # with them, and stopped before it was up. Keys made twice
            # are a database nobody can read, so it is carried on with
            # what is there: the seeder creates nobody a second time.
            self.say("Carrying on with the install that was begun…")
            port = self.settings.begun_port()
        else:
            self.say("Making this install's keys…")
            private, public = self._service_keys(release.images["backend"])
            keys, active = self._encryption_keys(release.images["backend"])
            self.settings.write(
                port=port, organization=name or "DecentAI",
                token_key=secrets.token_urlsafe(48),
                service_private_key=private, service_public_key=public,
                encryption_keys=keys, encryption_active=active)

        self._use(release.images, port)
        self.say("Preparing the database…")
        self._seed({"ADMIN_EMAIL": email, "ADMIN_PASSWORD": password,
                    "ADMIN_NAME": name or email.split("@", 1)[0]})
        self.say("Starting…")
        self.engine.compose("up", "-d", long=True)
        self._wait_until_serving()
        self.settings.record(
            version=release.version, images=release.images, port=port,
            signed=release.signed, installed_at=self._now(), previous=None,
            first_person=email)
        return self.settings.address

    @classmethod
    def first_person_problems(cls, email: str, password: str) -> List[str]:
        problems = []
        if not cls.EMAIL_RE.match(str(email or "")):
            problems.append("That is not an email address.")
        password = str(password or "")
        if len(password) < cls.PASSWORD_MIN:
            problems.append(f"The password must be at least {cls.PASSWORD_MIN} characters.")
        if password.strip() != password:
            problems.append("The password cannot start or end with a space.")
        if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
            problems.append("The password must have a letter and a number in it.")
        return problems

    # ------------------------------------------------------------------
    # Every day
    # ------------------------------------------------------------------

    def start(self) -> str:
        self._must_be_installed()
        self._must_reach_the_engine()
        self.settings.bring_up_to_date()
        self._use(self.settings.install()["images"])
        self.engine.compose("up", "-d", long=True)
        self._wait_until_serving()
        return self.settings.address

    def stop(self) -> None:
        self._must_be_installed()
        self._use(self.settings.install()["images"])
        self.engine.compose("stop", long=True)

    def stop_everything(self) -> None:
        """End everything the agents are doing, by starting the runtime
        again — the way out when the runtime itself no longer answers,
        which a chat's own Stop cannot be."""
        self._must_be_installed()
        self._use(self.settings.install()["images"])
        self.engine.compose("restart", "ai-runtime", long=True)
        self._wait_until_serving()

    def status(self) -> Dict[str, object]:
        install = self.settings.install()
        if not install:
            return {"installed": False}
        self._use(install["images"])
        return {
            "installed": True,
            "version": install.get("version"),
            "signed": bool(install.get("signed")),
            "address": self.settings.address,
            "develop_folder": self.settings.develop_folder,
            "services": {service: self.engine.state_of(service)
                         for service in ("mongo", *self.SERVING)},
        }

    def develop(self, folder: str) -> str:
        """Hand the backend a folder whose git repositories may be agent
        sources — or take it back with ''. The backend alone is started
        again; chats and agents carry on."""
        self._must_be_installed()
        self._must_reach_the_engine()
        self.settings.develop(folder)
        self._use(self.settings.install()["images"])
        self.engine.compose("up", "-d", "backend", long=True)
        self._wait_until_serving()
        if not folder:
            return "Agent sources come from repositories' addresses only again."
        return (f"The backend reads {folder} as {Settings.DEVELOP_TARGET}. On "
                f"Agents → Marketplace, add a source whose address is "
                f"{Settings.DEVELOP_TARGET} when that folder is your "
                f"repository, or {Settings.DEVELOP_TARGET}/<its folder> for "
                f"a repository inside it.")

    # ------------------------------------------------------------------
    # An update
    # ------------------------------------------------------------------

    def update(self, release: Release, again: bool = False) -> str:
        """Install ``release`` over what is here. Returns what was done,
        in a sentence. ``again`` installs a release that is not newer."""
        self._must_be_installed()
        self._must_reach_the_engine()
        install = self.settings.install()
        if not again and not release.newer_than(str(install.get("version"))):
            return (f"Version {install.get('version')} is installed, and "
                    f"{release.version} is not newer.")

        before = dict(install["images"])
        self._pull(release)
        self._use(before)
        self.engine.compose("up", "-d", "mongo", long=True)
        self.say("Keeping a copy of the database…")
        copy = self.backup(str(install.get("version")))

        self.say(f"Installing {release.version}…")
        self.engine.compose("stop", *self.SERVING, long=True)
        self.settings.bring_up_to_date()
        try:
            self._use(release.images)
            # Who the first person was, and not their password: the
            # seeder finds them, and creates nobody.
            self._seed({"ADMIN_EMAIL": str(install.get("first_person") or "")})
            self.engine.compose("up", "-d", long=True)
            self._wait_until_serving()
        except (EngineError, InstallationError) as failed:
            self.say(f"{release.version} did not come up: {failed}")
            self.say(f"Putting {install.get('version')} back…")
            self._put_back(before, copy)
            raise InstallationError(
                f"{release.version} did not come up, and "
                f"{install.get('version')} was put back as it was. "
                f"What went wrong: {failed}") from failed

        self.settings.record(
            version=release.version, images=release.images,
            signed=release.signed, updated_at=self._now(),
            previous={"version": install.get("version"), "images": before})
        return f"{release.version} is installed."

    def reset_password(self, email: str, password: str) -> str:
        """A new password for a person of this install, set by the
        platform's own script (bootstrap/reset_password.py) in the
        backend's image — what a reset link does, for an install with no
        email to send one. The password is said once, to the script, and
        kept nowhere. Returns what was done, in a sentence."""
        self._must_be_installed()
        self._must_reach_the_engine()
        email = str(email or "").strip() or str(
            self.settings.install().get("first_person") or "")
        problems = self.first_person_problems(email, password)
        if problems:
            raise InstallationError(" ".join(problems))
        self._use(self.settings.install()["images"])
        self.engine.compose("up", "-d", "mongo", long=True)
        try:
            self.engine.compose(
                "--profile", "seed", "run", "--rm", "init",
                "python", "/opt/decentai/bootstrap/reset_password.py",
                extra={"RESET_EMAIL": email, "RESET_PASSWORD": password},
                long=True)
        except EngineError as failed:
            raise InstallationError(
                f"The password was not changed. {self._said_by_the_script(failed)}"
            ) from failed
        return f"The password of {email} is set. Sign in with it."

    @staticmethod
    def _said_by_the_script(failed: Exception) -> str:
        """The script's own reason, where the engine passed it on."""
        text = str(failed)
        for line in reversed(text.splitlines()):
            if line.strip().startswith("ERROR:"):
                return line.strip()[len("ERROR:"):].strip()
        return text

    def backup(self, label: str) -> Path:
        """A copy of the database, as the database writes one. The last
        few are kept."""
        raw = self.engine.compose_bytes(
            "exec", "-T", "mongo", "sh", "-c",
            self.DUMP.format(name=Settings.DATABASE_NAME))
        if not raw:
            raise InstallationError("The database gave an empty copy of itself.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.settings.backups / f"{stamp}-{label}.archive.gz"
        target.write_bytes(raw)
        for old in sorted(self.settings.backups.glob("*.archive.gz"))[:-self.BACKUPS_KEPT]:
            old.unlink()
        return target

    def uninstall(self, keep: bool = False) -> Optional[Path]:
        """Take the stack off this computer: its containers, its
        network and its volumes — the database, uploaded files, approved
        agents and agent environments. With ``keep``, a copy of the
        database is made first and its path returned; it is in the
        launcher's own folder, which whoever started the launcher
        removes last, after taking the copy out.

        An install that was begun and never finished is removed the
        same way."""
        if not self.settings.installed and not self.settings.begun:
            raise InstallationError("DecentAI is not installed here.")
        self._must_reach_the_engine()
        images = self.settings.install().get("images") or {
            part: "none" for part in ("backend", "runtime", "frontend")}
        self._use(images)
        kept = None
        if keep and self.settings.installed:
            self.say("Keeping a copy of the database…")
            self.engine.compose("up", "-d", "mongo", long=True)
            kept = self.backup(
                str(self.settings.install().get("version") or "uninstall"))
        self.say("Removing DecentAI…")
        self.engine.compose("--profile", "seed", "down", "--volumes",
                            "--remove-orphans", long=True)
        self.settings.forget()
        return kept

    def restore(self, copy: Path) -> None:
        self.engine.compose(
            "exec", "-T", "mongo", "sh", "-c", self.RESTORE,
            long=True, stdin=Path(copy).read_bytes())

    def _put_back(self, images: Dict[str, str], copy: Path) -> None:
        self._use(images)
        self.engine.compose("stop", *self.SERVING, long=True)
        self.engine.compose("up", "-d", "mongo", long=True)
        # The new release's seeder may have changed the database before
        # the release failed: the copy is what the old one can read.
        self.restore(copy)
        self.engine.compose("up", "-d", long=True)
        self._wait_until_serving()

    # ------------------------------------------------------------------
    def _use(self, images: Dict[str, str], port: Optional[int] = None) -> None:
        self.engine.environment = self.settings.stack_environment(images, port)
        self.settings.name_lookups(self.engine.is_podman())
        # A folder somebody develops in stays handed over across starts
        # and updates, until they take it back.
        self.engine.overrides = [self.settings.develop_file,
                                 self.settings.names_file]

    def _pull(self, release: Release) -> None:
        for part, image in release.images.items():
            # A developer's own build is on this machine and nowhere
            # else. A signed release names its images by what they are.
            if not release.signed and self.engine.has(image):
                continue
            self.say(f"Downloading the {part}…")
            self.engine.pull(image)

    def _seed(self, first_person: Dict[str, str]) -> None:
        self.engine.compose("--profile", "seed", "run", "--rm", "init",
                            extra=first_person, long=True)

    def _service_keys(self, image: str) -> tuple:
        said = self.engine.run(image, [
            "python", "/opt/decentai/bootstrap/generate_service_keys.py"])
        return (self._line(said, "BACKEND_SERVICE_PRIVATE_KEY"),
                self._line(said, "BACKEND_SERVICE_PUBLIC_KEY"))

    def _encryption_keys(self, image: str) -> tuple:
        said = self.engine.run(image, [
            "python", "/opt/decentai/bootstrap/generate_secret_keys.py"])
        return (self._line(said, "SECRET_ENCRYPTION_KEYS"),
                self._line(said, "SECRET_ENCRYPTION_ACTIVE"))

    @staticmethod
    def _line(said: str, name: str) -> str:
        for line in str(said).splitlines():
            if line.startswith(name + "="):
                value = line[len(name) + 1:].strip()
                if value:
                    return value
        raise InstallationError(f"The platform did not make {name}.")

    def _wait_until_serving(self) -> None:
        deadline = time.monotonic() + self.HEALTHY_SECONDS
        states: Dict[str, str] = {}
        while True:
            states = {s: self.engine.state_of(s) for s in self.SERVING}
            if all(state == "healthy" for state in states.values()):
                return
            stopped = [s for s, state in states.items() if state in ("exited", "absent")]
            if stopped:
                raise InstallationError(
                    f"{', '.join(stopped)} stopped instead of starting.")
            if time.monotonic() > deadline:
                waiting = [f"{s} is {state}" for s, state in states.items()
                           if state != "healthy"]
                raise InstallationError(
                    "It did not come up in time: " + ", ".join(waiting) + ".")
            time.sleep(self.pause)

    def _must_be_installed(self) -> None:
        if not self.settings.installed:
            raise InstallationError(
                "DecentAI is not installed here yet. `install` installs it.")

    def _must_reach_the_engine(self) -> None:
        why = self.engine.reachable()
        if why:
            raise InstallationError(
                f"The container engine does not answer: {why}. Is Docker "
                f"or Podman running, and was the launcher given its socket?")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
