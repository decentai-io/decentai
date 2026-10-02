"""The launcher's tests: no engine, no network.

    cd launcher && python -m pytest tests -q

An engine that remembers what it was asked stands where Docker would.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from launcher.engine import Engine  # noqa: E402


class FakeEngine:
    """Stands where the engine's command line would: remembers every
    command and the environment it ran in, and answers as a test says."""

    SERVICE_KEYS = ("# backend/config.env\n"
                    "BACKEND_SERVICE_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\\nprivate\\n\n\n"
                    "# ai_runtime/config.env\n"
                    "BACKEND_SERVICE_PUBLIC_KEY=-----BEGIN PUBLIC KEY-----\\npublic\\n\n")
    ENCRYPTION_KEYS = "SECRET_ENCRYPTION_KEYS=01:encryption-key\nSECRET_ENCRYPTION_ACTIVE=01\n"

    def __init__(self):
        self.asked = []
        #: the images on this machine
        self.present = set()
        self.pulled = []
        #: service -> "status health", as `docker inspect` prints it
        self.states = {}
        #: image -> what every service is when that image is the backend's
        self.states_with = {}
        self.unreachable = ""
        #: whether the engine behind the socket says it is Podman
        self.podman = False
        self.database = b"the database, as it was"
        self.restored = []
        self.fails = {}

    def __call__(self, argv, environment, stdin):
        self.asked.append({"argv": list(argv), "environment": dict(environment),
                           "stdin": stdin})
        words = " ".join(argv)
        for fragment, why in self.fails.items():
            if fragment in words:
                return 1, "", why
        if argv[1] == "version" and "Components" in words:
            return 0, "Podman Engine;Conmon;" if self.podman else "Engine;containerd;", ""
        if argv[1] == "version":
            return (1, "", self.unreachable) if self.unreachable else (0, "27.0\n", "")
        if argv[1:3] == ["image", "inspect"]:
            return (0, "[]", "") if argv[3] in self.present else (1, "", "no such image")
        if argv[1] == "pull":
            self.pulled.append(argv[2])
            self.present.add(argv[2])
            return 0, "", ""
        if argv[1] == "run":
            if "generate_service_keys.py" in words:
                return 0, self.SERVICE_KEYS, ""
            if "generate_secret_keys.py" in words:
                return 0, self.ENCRYPTION_KEYS, ""
            return 0, "", ""
        if argv[1] == "inspect":
            service = argv[-1].replace("container-of-", "")
            running = self.states_with.get(environment.get("BACKEND_IMAGE"), self.states)
            return 0, running.get(service, "running healthy") + "\n", ""
        if argv[1] == "compose":
            rest = self.after_files(argv)
            if rest[:3] == ["ps", "-a", "-q"]:
                return 0, f"container-of-{rest[3]}\n", ""
            if "mongodump" in words:
                return 0, self.database, ""
            if "mongorestore" in words:
                self.restored.append(stdin)
                return 0, "", ""
            return 0, "", ""
        return 0, "", ""

    @staticmethod
    def after_files(argv):
        """A compose command's words after its files: `-f <file>`, once
        or more."""
        rest = list(argv[2:])
        while rest[:1] == ["-f"]:
            rest = rest[2:]
        return rest

    @staticmethod
    def files(asked):
        """The compose files a command was given, in order."""
        argv, found = asked["argv"][2:], []
        while argv[:1] == ["-f"]:
            found.append(argv[1])
            argv = argv[2:]
        return found

    # -- what a test asks of it -----------------------------------------
    def stack(self):
        """The stack's commands, in order, as words after the files."""
        return [" ".join(self.after_files(asked["argv"])) for asked in self.asked
                if asked["argv"][1] == "compose"
                and self.after_files(asked["argv"])[0] not in ("ps",)
                and "mongodump" not in " ".join(asked["argv"])
                and "mongorestore" not in " ".join(asked["argv"])]

    def environment_of(self, fragment):
        return next(asked["environment"] for asked in self.asked
                    if fragment in " ".join(asked["argv"]))


@pytest.fixture
def engine():
    fake = FakeEngine()
    Engine.runner = fake
    yield fake
    Engine.runner = None
