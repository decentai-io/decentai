"""The launcher's command line.

    install           the first run: keys, the first person, the stack
    start / stop      every day
    status            what is installed, and what is running
    update            a newer release, by one word
    uninstall         take it off this computer
    backup            a copy of the database, now
    stop-everything   end everything the agents are doing
    develop           a folder of your own agents, as a source
    reset-password    a new password, where there is no email to send a link

The launcher is a container, given the engine's socket and a volume of
its own:

    docker run --rm -it \\
        -v /var/run/docker.sock:/var/run/docker.sock \\
        -v decentai_launcher:/state \\
        decentai-launcher install
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from launcher import VERSION
from launcher.engine import Engine, EngineError
from launcher.installation import Installation, InstallationError
from launcher.release import Keys, Release, ReleaseError
from launcher.settings import Settings, SettingsError

HERE = Path(__file__).resolve().parent.parent


class CommandLine:
    STATE = "/state"
    COMPOSE = HERE / "compose.yml"
    KEYS = HERE / "keys"
    RELEASE = HERE / "release.json"
    #: The address a published launcher reads the current release from.
    PUBLISHED = "DECENTAI_RELEASE_URL"

    def __init__(self, arguments: argparse.Namespace):
        self.arguments = arguments
        self.settings = Settings(os.environ.get("DECENTAI_STATE") or self.STATE)
        self.installation = Installation(
            self.settings, Engine(self.COMPOSE), self.say)

    @staticmethod
    def say(text: str) -> None:
        print(text, flush=True)

    # ------------------------------------------------------------------
    def source(self) -> str:
        """Where the release is read from: what the person named; else
        where this launcher's releases are published, which a published
        launcher is built knowing; else the file it was built with — a
        build of one's own."""
        return (self.arguments.release
                or os.environ.get(self.PUBLISHED) or str(self.RELEASE))

    def release(self) -> Release:
        release = Release.read(self.source(), Keys(self.KEYS).all(),
                               unsigned=self.arguments.unsigned)
        if Release.number(release.minimum_launcher) > Release.number(VERSION):
            raise ReleaseError(
                f"Release {release.version} needs launcher "
                f"{release.minimum_launcher} or later; this is {VERSION}. "
                f"The current starter brings it: download it again from "
                f"where you got this one.")
        if not release.signed:
            self.say("This release is NOT signed: it is a build somebody made "
                     "themselves, installed because you said --unsigned.")
        return release

    def install(self) -> int:
        release = self.release()
        email = self.arguments.email or input("Your email: ").strip()
        password = os.environ.get("DECENTAI_PASSWORD") or ""
        if not password:
            password = getpass.getpass("A password (10 characters or more, "
                                       "a letter and a number): ")
            if getpass.getpass("The password again: ") != password:
                raise InstallationError("The two passwords are not the same.")
        address = self.installation.first_run(
            release, email, password, self.arguments.name or "",
            self.arguments.port)
        self.say(f"\nDecentAI {release.version} is running. Open {address}")
        self.say(f"Sign in as {email}.")
        return 0

    def start(self) -> int:
        self.say(f"DecentAI is running. Open {self.installation.start()}")
        return 0

    def stop(self) -> int:
        self.installation.stop()
        self.say("DecentAI is stopped. Nothing of yours was removed.")
        return 0

    def status(self) -> int:
        status = self.installation.status()
        if self.arguments.json:
            # For the desktop app, which shows it and does not read prose.
            status["begun"] = self.settings.begun
            status["launcher"] = VERSION
            status["first_person"] = str(
                self.settings.install().get("first_person") or "")
            status["images"] = self.settings.install().get("images") or {}
            self.say(json.dumps(status, sort_keys=True))
            return 0
        if not status["installed"]:
            self.say("DecentAI is not installed here.")
            return 1
        signed = "" if status["signed"] else " (not signed)"
        self.say(f"DecentAI {status['version']}{signed} — {status['address']}")
        for service, state in status["services"].items():
            self.say(f"  {service:12} {state}")
        if status.get("develop_folder"):
            self.say(f"  Developing agents in {status['develop_folder']}")
        return 0

    def develop(self) -> int:
        if self.arguments.off:
            self.say(self.installation.develop(""))
            return 0
        if not self.arguments.folder:
            folder = self.settings.develop_folder
            self.say(f"Developing agents in {folder}." if folder else
                     "No folder is handed over. `develop <folder>` hands one.")
            return 0
        self.say(self.installation.develop(self.arguments.folder))
        return 0

    def update(self) -> int:
        release = self.release()
        installed = self.settings.install().get("version")
        if not self.arguments.yes and release.newer_than(str(installed)):
            self.say(f"Version {release.version} is available; {installed} is "
                     f"installed. The update takes about a minute, and chats "
                     f"that are working are interrupted.")
            if release.notes:
                self.say(f"What changed: {release.notes}")
            if input("Update now? (yes/no) ").strip().lower() not in ("y", "yes"):
                self.say("Nothing was changed.")
                return 0
        self.say(self.installation.update(release, again=self.arguments.again))
        return 0

    def uninstall(self) -> int:
        if not self.arguments.yes:
            self.say("This removes DecentAI from this computer: its chats, "
                     "files, agents, saved credentials and its keys. It "
                     "cannot be undone.")
            if input("Remove it? (yes/no) ").strip().lower() not in ("y", "yes"):
                self.say("Nothing was removed.")
                return 0
        kept = self.installation.uninstall(keep=self.arguments.keep)
        if kept is not None:
            self.say(f"Kept: {kept.name}")
        self.say("DecentAI was removed.")
        return 0

    def backup(self) -> int:
        version = str(self.settings.install().get("version") or "unknown")
        self.installation._must_be_installed()
        self.installation._use(self.settings.install()["images"])
        self.say(f"Kept: {self.installation.backup(version).name}")
        return 0

    def reset_password(self) -> int:
        installed = self.settings.install().get("first_person") or ""
        email = self.arguments.email or ""
        if not email:
            asked = input(f"Whose password? ({installed}) " if installed
                          else "Whose password? (email) ").strip()
            email = asked or installed
        password = os.environ.get("DECENTAI_PASSWORD") or ""
        if not password:
            password = getpass.getpass("The new password (10 characters or "
                                       "more, a letter and a number): ")
            if getpass.getpass("The new password again: ") != password:
                raise InstallationError("The two passwords are not the same.")
        self.say(self.installation.reset_password(email, password))
        return 0

    def stop_everything(self) -> int:
        self.installation.stop_everything()
        self.say("Everything the agents were doing has ended, and the "
                 "runtime is up again.")
        return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="launcher", description="DecentAI on this computer.")
    top.add_argument("--version", action="version", version=VERSION)
    commands = top.add_subparsers(dest="command", required=True)

    def release_options(command):
        command.add_argument("--release", help="a release file: a path or an https address")
        command.add_argument("--unsigned", action="store_true",
                             help="take a release nobody signed — a build of your own")

    install = commands.add_parser("install", help="the first run")
    release_options(install)
    install.add_argument("--email")
    install.add_argument("--name", help="your name, or your organization's")
    install.add_argument("--port", type=int, default=Settings.DEFAULT_PORT)

    update = commands.add_parser("update", help="install a newer release")
    release_options(update)
    update.add_argument("--yes", action="store_true", help="do not ask first")
    update.add_argument("--again", action="store_true",
                        help="install it even if it is not newer")

    develop = commands.add_parser(
        "develop", help="let the git repositories in a folder of yours be agent sources")
    develop.add_argument("folder", nargs="?",
                         help="the folder, as the engine finds it (the starter says it so)")
    develop.add_argument("--off", action="store_true", help="take the folder back")

    reset = commands.add_parser(
        "reset-password", help="a new password, where there is no email to send a link")
    reset.add_argument("--email", help="whose; the first person's when left out")

    status = commands.add_parser("status", help="what is installed, and what is running")
    status.add_argument("--json", action="store_true",
                        help="as one line of JSON, for a program to read")

    uninstall = commands.add_parser("uninstall", help="take it off this computer")
    uninstall.add_argument("--yes", action="store_true", help="do not ask first")
    uninstall.add_argument("--keep", action="store_true",
                           help="keep a copy of the database first")

    for name, text in (("start", "start what is installed"),
                       ("stop", "stop it; nothing is removed"),
                       ("backup", "a copy of the database, now"),
                       ("stop-everything", "end everything the agents are doing")):
        commands.add_parser(name, help=text)
    return top


def main(argv=None) -> int:
    arguments = parser().parse_args(argv)
    for name, default in (("release", None), ("unsigned", False), ("json", False)):
        if not hasattr(arguments, name):
            setattr(arguments, name, default)
    try:
        return getattr(CommandLine(arguments),
                       arguments.command.replace("-", "_"))()
    except (InstallationError, ReleaseError, SettingsError, EngineError) as failed:
        print(f"\n{failed}", file=sys.stderr, flush=True)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Nothing further was changed.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
