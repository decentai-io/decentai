"""The container engine, as the launcher speaks to it.

The launcher is itself a container, handed the engine's socket. It
speaks through the engine's own command line — `docker`, and `docker
compose` for the stack — because that is the one interface Docker and
Podman both answer, and the one a person can repeat by hand when
something has to be understood.

    engine = Engine(compose_file, project_environment)
    engine.reachable()                 "" or why not
    engine.compose("up", "-d")         the stack
    engine.has(image) / engine.pull(image)
    engine.state_of(service)           running, healthy, exited, absent
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, ClassVar, Dict, List, Optional, Sequence, Tuple


class EngineError(RuntimeError):
    """The engine refused, or could not be asked. The message is
    written for the person at the keyboard."""


class Engine:
    COMMAND = "docker"
    #: Pulling a browser takes as long as the person's connection says.
    LONG_SECONDS = 3600
    SHORT_SECONDS = 120

    #: Test seam — a callable (argv, environment, stdin) returning
    #: (return_code, stdout, stderr), text or bytes. Set it and no
    #: command runs.
    runner: ClassVar[Optional[Callable[..., tuple]]] = None

    def __init__(self, compose_file: str | Path,
                 environment: Optional[Dict[str, str]] = None):
        self.compose_file = Path(compose_file)
        #: What the compose file is filled in with: the images, the
        #: port, the launcher's folder.
        self.environment = dict(environment or {})
        #: Files laid over the compose file, each only while it exists:
        #: what one install says of itself (a folder it develops in).
        self.overrides: List[Path] = []

    # ------------------------------------------------------------------
    def reachable(self) -> str:
        """Why the engine cannot be spoken to, or '' when it can."""
        try:
            code, _, said = self._run([self.COMMAND, "version", "--format",
                                       "{{.Server.Version}}"], self.SHORT_SECONDS)
        except EngineError as exc:
            return str(exc)
        if code != 0:
            return (said.strip().splitlines() or ["the engine did not answer"])[-1]
        return ""

    def is_podman(self) -> bool:
        """Whether the engine behind the socket is Podman, which
        answers the same command line and says who it is when asked."""
        try:
            code, said, _ = self._run(
                [self.COMMAND, "version", "--format",
                 "{{range .Server.Components}}{{.Name}};{{end}}"],
                self.SHORT_SECONDS)
        except EngineError:
            return False
        return code == 0 and "podman" in said.lower()

    # -- images --------------------------------------------------------
    def has(self, image: str) -> bool:
        code, _, _ = self._run(
            [self.COMMAND, "image", "inspect", image], self.SHORT_SECONDS)
        return code == 0

    def pull(self, image: str) -> None:
        self._must([self.COMMAND, "pull", image], self.LONG_SECONDS,
                   f"{image} could not be pulled")

    def run(self, image: str, arguments: Sequence[str]) -> str:
        """One command in a container of its own, gone when it ends.
        Returns what it printed."""
        return self._must(
            [self.COMMAND, "run", "--rm", image, *arguments], self.SHORT_SECONDS,
            f"{image} could not run {' '.join(arguments)[:80]}")

    # -- the stack -----------------------------------------------------
    def compose(self, *arguments: str, extra: Optional[Dict[str, str]] = None,
                long: bool = False, stdin: Optional[bytes] = None) -> str:
        return self._must(
            self._compose(arguments), self.LONG_SECONDS if long else self.SHORT_SECONDS,
            f"the stack could not {arguments[0]}", extra=extra, stdin=stdin)

    def compose_bytes(self, *arguments: str) -> bytes:
        """As ``compose``, for what is not text: a database's archive."""
        code, said, complained = self._execute(
            self._compose(arguments), self.LONG_SECONDS)
        if code != 0:
            raise EngineError(
                f"the stack could not {arguments[0]}: "
                + complained.decode("utf-8", "replace").strip()[-400:])
        return said

    def container_of(self, service: str) -> str:
        """The service's container, or '' when it has none."""
        found = self.compose("ps", "-a", "-q", service).strip().splitlines()
        return found[0] if found else ""

    def state_of(self, service: str) -> str:
        """``healthy``, ``starting``, ``unhealthy``, ``running`` (it has
        no check of its own), ``exited``, or ``absent``."""
        container = self.container_of(service)
        if not container:
            return "absent"
        code, said, _ = self._run(
            [self.COMMAND, "inspect", "--format",
             "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
             container], self.SHORT_SECONDS)
        if code != 0:
            return "absent"
        status, _, health = said.strip().partition(" ")
        # A container that is not running has no health to speak of:
        # what it last said of itself is not what it is.
        if status != "running":
            return status or "absent"
        return health or status

    # ------------------------------------------------------------------
    def _compose(self, arguments: Sequence[str]) -> List[str]:
        files = ["-f", str(self.compose_file)]
        for override in self.overrides:
            if Path(override).is_file():
                files += ["-f", str(override)]
        return [self.COMMAND, "compose", *files, *arguments]

    def _environment(self, extra: Optional[Dict[str, str]]) -> Dict[str, str]:
        import os

        return {**os.environ, **self.environment, **(extra or {})}

    def _must(self, argv: Sequence[str], timeout: int, what: str,
              extra: Optional[Dict[str, str]] = None,
              stdin: Optional[bytes] = None) -> str:
        code, said, complained = self._run(argv, timeout, extra, stdin)
        if code != 0:
            why = (complained or said).strip()[-400:] or f"exit {code}"
            raise EngineError(f"{what}: {why}")
        return said

    def _run(self, argv: Sequence[str], timeout: int,
             extra: Optional[Dict[str, str]] = None,
             stdin: Optional[bytes] = None) -> Tuple[int, str, str]:
        code, said, complained = self._execute(argv, timeout, extra, stdin)
        return (code, said.decode("utf-8", "replace"),
                complained.decode("utf-8", "replace"))

    def _execute(self, argv: Sequence[str], timeout: int,
                 extra: Optional[Dict[str, str]] = None,
                 stdin: Optional[bytes] = None) -> Tuple[int, bytes, bytes]:
        environment = self._environment(extra)
        if Engine.runner is not None:
            code, said, complained = Engine.runner(list(argv), environment, stdin)
            return code, self._as_bytes(said), self._as_bytes(complained)
        try:
            done = subprocess.run(
                list(argv), capture_output=True, env=environment,
                input=stdin, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise EngineError(f"the engine could not be asked: {exc}") from exc
        return done.returncode, done.stdout, done.stderr

    @staticmethod
    def _as_bytes(said) -> bytes:
        return said if isinstance(said, bytes) else str(said or "").encode("utf-8")
