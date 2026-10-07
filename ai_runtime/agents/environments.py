"""An agent's private environment (docs/reference/worker-protocol.md).

    <install_dir>/envs/<hex>/     the venv its worker runs in

Built at the install station, after the manifest is proven approved:
a real venv, the SDK copied in, and the venv's own pip installing
exactly the declared dependency list. An environment is named by that
list (AgentLibrary.environment_key), so two packages that declare the
same one share it — an update that changes code only needs no pip —
and two agents pinning incompatible versions of one package are two
folders, not a conflict.

The SDK is COPIED, not pip-installed: it is dependency-free pure Python
by contract, so copying it into site-packages is the whole of
installing it, and there is no packaging ceremony to drift.

``.ready`` is written last. A crash mid-build leaves a folder without
the marker, and ``exists()`` stays false — the same never-half-here
rule the package store follows.

WHO BUILDS. A package may run code of its own while it is built, and
that code is nobody's the runtime trusts. Where workers are confined
(confinement.py) the declared list is handed to the builder: a user of
its own, which reaches where packages come from and nothing else, and
leaves what it downloaded and built as wheels. The runtime takes the
wheels and unpacks them, which runs nothing of theirs. Where nothing
confines, the venv's pip installs the list as it always has.

PACKAGES FOR ONE RUN. A function that runs code a person allowed may
need packages no manifest named (``extras``):

    <install_dir>/envs/<hex>/extras/<hex>/    one list, installed flat

A folder per list, built the way the environment's own packages are and
kept for the next run that names the same list. It lies inside the
environment, so a worker that reads the one reads the other, and it
goes when the environment goes.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ai_runtime.runtime_logging import RuntimeLoggerFactory

READY_MARKER = ".ready"
#: What a wheel is called (its name says what it holds and what it runs
#: on), and so what is taken of what the builder left.
WHEEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*\.whl$")
#: What a package is asked for as, where a person reads the list and
#: allows it: a name, extras in brackets, and versions. Not an address,
#: not a path, not an option.
_VERSION = r"(==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!_-]+"
REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?(\[[A-Za-z0-9._,-]+\])?"
    rf"({_VERSION}(,{_VERSION})*)?$")


class AgentEnvironment:
    CREATE_TIMEOUT_SECONDS = 300
    PIP_TIMEOUT_SECONDS = 300
    #: Where the builder's wheels wait to be unpacked, inside the
    #: environment being built and gone before it is ready.
    WHEELS_FOLDER = ".wheels"

    #: What a build is given of this process's environment: what a
    #: program needs to run at all, and where whoever runs it said
    #: packages come from. Nothing else of the runtime's.
    BUILDER_VARIABLES = (
        "PATH", "LANG", "LC_ALL",
        "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST", "PIP_CERT",
    )

    #: Where lists of packages for one run are installed, inside the
    #: environment, and how many names one list may hold.
    EXTRAS_FOLDER = "extras"
    EXTRAS_MAX = 30
    #: One list is installed at a time: two calls naming the same list
    #: would otherwise build one folder twice, over each other.
    extras_lock = threading.Lock()

    #: Test seam — a callable taking the argv list and returning
    #: (return_code, output). Set it and no subprocess runs.
    runner: Optional[Callable[[Sequence[str]], tuple]] = None

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    @property
    def python(self) -> Path:
        """The venv's interpreter — the one a worker is spawned with."""
        windows = self.root / "Scripts" / "python.exe"
        posix = self.root / "bin" / "python"
        return windows if windows.exists() or os.name == "nt" else posix

    def exists(self) -> bool:
        """Whether a COMPLETE environment is here. The marker is written
        after the last build step, so half-built never answers yes."""
        return (self.root / READY_MARKER).is_file()

    # ------------------------------------------------------------------
    def build(self, dependencies: List[str], place=None) -> List[str]:
        """Create the venv, copy the SDK in, install the declared list.
        Returns errors, empty on success; any failure removes the
        folder — an environment is whole or absent, never partial. One
        that exists is kept, with its SDK brought up to the platform's.

        ``place`` is the builder's (confinement.py), where the list is
        downloaded and built; None where nothing confines."""
        if self.exists():
            return self.refresh_sdk()
        self.remove()

        errors = (
            self._create_venv()
            or self._copy_sdk()
            or self._install(dependencies, place)
        )
        if errors:
            self.remove()
            return errors

        (self.root / READY_MARKER).write_text("", encoding="utf-8")
        self.logger.info(f"Environment ready: {self.root.name[:12]}")
        return []

    def remove(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    # ------------------------------------------------------------------
    def _create_venv(self) -> List[str]:
        code, output = self._run(
            [sys.executable, "-m", "venv", str(self.root)],
            self.CREATE_TIMEOUT_SECONDS,
        )
        if code != 0:
            return [f"environment creation failed: {output.strip()[-400:]}"]
        return []

    #: Which SDK this environment carries — a digest of the platform's
    #: SDK sources at the last copy, so a refresh is one comparison.
    SDK_MARKER = ".sdk"

    @staticmethod
    def _sdk_source() -> Path:
        import decentai_sdk

        return Path(decentai_sdk.__file__).resolve().parent

    @classmethod
    def sdk_digest(cls) -> str:
        digest = hashlib.sha256()
        for path in sorted(cls._sdk_source().glob("*.py")):
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def _copy_sdk(self) -> List[str]:
        target = self._site_packages()
        if target is None:
            return ["environment has no site-packages to receive the SDK"]
        try:
            shutil.rmtree(target / "decentai_sdk", ignore_errors=True)
            shutil.copytree(
                self._sdk_source(), target / "decentai_sdk",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        except OSError as exc:
            return [f"SDK copy failed: {exc}"]
        (self.root / self.SDK_MARKER).write_text(
            self.sdk_digest(), encoding="utf-8")
        return []

    def refresh_sdk(self) -> List[str]:
        """The SDK copied in at build is the SDK of that day, and the
        platform's moves on — a feature on the wire, a fix. An
        environment carries the platform's current SDK before a worker
        is spawned from it: one digest compared with the marker, and a
        copy only when they differ. Returns errors, empty when current."""
        if not self.exists():
            return []
        marker = self.root / self.SDK_MARKER
        current = self.sdk_digest()
        try:
            if marker.read_text(encoding="utf-8").strip() == current:
                return []
        except OSError:
            pass
        errors = self._copy_sdk()
        if not errors:
            self.logger.info(
                f"SDK refreshed in environment {self.root.name[:12]}")
        return errors

    def _site_packages(self) -> Optional[Path]:
        candidates = [self.root / "Lib" / "site-packages"]
        candidates += sorted((self.root / "lib").glob("python*/site-packages"))
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return None

    def _install(self, dependencies: List[str], place=None) -> List[str]:
        dependencies = sorted({str(d).strip() for d in dependencies or []
                               if str(d).strip()})
        if not dependencies:
            return []
        # A requirement names a package. What begins as an option would
        # be read as one, by a program that takes its orders from there.
        options = [d for d in dependencies if d.startswith("-")]
        if options:
            return [f"dependency installation failed: '{options[0]}' is "
                    f"not a requirement"]
        if place is not None:
            return self._install_built(dependencies, place)
        code, output = self._run(
            [str(self.python), "-m", "pip", "install",
             "--no-input", "--disable-pip-version-check",
             *dependencies],
            self.PIP_TIMEOUT_SECONDS,
        )
        return self._said(code, output)

    @staticmethod
    def _said(code: int, output: str) -> List[str]:
        if code != 0:
            # The tail is what names the conflict or the missing wheel;
            # the whole log would drown the page it is shown on.
            return [f"dependency installation failed: {str(output).strip()[-400:]}"]
        return []

    # ------------------------------------------------------------------
    # Built by the builder, unpacked by the runtime
    # ------------------------------------------------------------------

    def _install_built(self, dependencies: List[str], place) -> List[str]:
        """The declared list, downloaded and built where that is
        confined, and then unpacked here. The list itself is read by
        the builder's pip and never by the runtime's, which is handed
        files: a wheel is unpacked, and nothing of it is run."""
        wheels = self.root / self.WHEELS_FOLDER
        errors = self._build_wheels(dependencies, place, wheels)
        if not errors:
            code, output = self._run(
                [str(self.python), "-m", "pip", "install",
                 "--no-input", "--disable-pip-version-check",
                 "--no-index", "--no-deps", "--no-cache-dir",
                 *[str(wheel) for wheel in sorted(wheels.iterdir())]],
                self.PIP_TIMEOUT_SECONDS,
            )
            errors = self._said(code, output)
        shutil.rmtree(wheels, ignore_errors=True)
        return errors

    def _build_wheels(self, dependencies: List[str], place,
                      wheels: Path) -> List[str]:
        """Every package the list needs, as wheels in ``wheels``. One
        build at a time: builds share a user, and ending one ends
        everything that user runs."""
        with place.confinement.building_lock:
            errors = place.prepare()
            if errors:
                return errors
            place.admit("the builder", place.confinement.package_network())
            try:
                # Nothing is kept between builds: what one build left
                # would be what the next one installs.
                code, output = place.run(
                    [str(self.python), "-m", "pip", "wheel",
                     "--no-input", "--disable-pip-version-check",
                     "--no-cache-dir", "--wheel-dir", str(place.spool),
                     *dependencies],
                    reads=[self.root],
                    environment=self._builder_environment(),
                    timeout=self.PIP_TIMEOUT_SECONDS,
                )
                return self._said(code, output) or self._take(place.spool, wheels)
            finally:
                place.dismiss()
                place.stop()
                place.clear()

    # ------------------------------------------------------------------
    # Packages for one run
    # ------------------------------------------------------------------

    @staticmethod
    def requirement(asked: str) -> str:
        """A package as it may be asked for, or '' when it is not one:
        ``pandas``, ``requests==2.32.3``, ``uvicorn[standard]>=0.30``."""
        asked = str(asked or "").strip()
        # Space is allowed round a version (`requests >= 2.32`) and
        # nowhere inside a name: `requests evil` is two words and not a
        # package called `requestsevil`.
        if re.search(r"[A-Za-z0-9_.\]-]\s+[A-Za-z0-9_.\[-]", asked):
            return ""
        written = "".join(asked.split())
        return written if REQUIREMENT_RE.match(written) else ""

    def extras(self, requirements: List[str],
               place=None) -> Tuple[Optional[Path], List[str]]:
        """A folder holding the list and everything it depends on, for
        a program to put on its path. Returns (the folder, errors): one
        built before is handed back as it is, and a build that fails
        leaves nothing.

        ``place`` is the builder's, as for the environment itself: the
        packages are downloaded and built there, and unpacked here."""
        wanted = sorted({self.requirement(r) for r in requirements or []})
        if not wanted or "" in wanted:
            return None, ["a package is asked for by its name and, where "
                          "it matters, its version: pandas, requests==2.32.3"]
        if len(wanted) > self.EXTRAS_MAX:
            return None, [f"one run may ask for at most {self.EXTRAS_MAX} packages"]
        digest = hashlib.sha256("\n".join(wanted).encode("utf-8")).hexdigest()
        folder = self.root / self.EXTRAS_FOLDER / digest[:32]
        with AgentEnvironment.extras_lock:
            if (folder / READY_MARKER).is_file():
                return folder, []
            shutil.rmtree(folder, ignore_errors=True)
            errors = self._install_extras(wanted, folder, place)
            if errors:
                shutil.rmtree(folder, ignore_errors=True)
                return None, errors
            (folder / READY_MARKER).write_text("", encoding="utf-8")
        self.logger.info(f"Packages ready: {', '.join(wanted)[:120]}")
        return folder, []

    def _install_extras(self, wanted: List[str], folder: Path,
                        place) -> List[str]:
        install = [str(self.python), "-m", "pip", "install", "--no-input",
                   "--disable-pip-version-check", "--target", str(folder)]
        try:
            folder.mkdir(parents=True)
        except OSError as exc:
            return [f"dependency installation failed: {exc}"]
        if place is None:
            return self._said(*self._run([*install, *wanted],
                                         self.PIP_TIMEOUT_SECONDS))
        wheels = folder / self.WHEELS_FOLDER
        errors = self._build_wheels(wanted, place, wheels)
        if not errors:
            # Unpacked by the runtime's own interpreter, isolated, never
            # by the environment's: that one already holds the agent's
            # declared packages, and starting it would run whatever
            # their .pth files say — here, as the runtime.
            errors = self._said(*self._run(
                [sys.executable, "-I", *install[1:],
                 "--no-index", "--no-deps", "--no-cache-dir",
                 *[str(wheel) for wheel in sorted(wheels.iterdir())]],
                self.PIP_TIMEOUT_SECONDS))
        shutil.rmtree(wheels, ignore_errors=True)
        return errors

    @classmethod
    def _builder_environment(cls) -> Dict[str, str]:
        return {name: os.environ[name] for name in cls.BUILDER_VARIABLES
                if os.environ.get(name)}

    @staticmethod
    def _take(source: Path, target: Path) -> List[str]:
        """Copy the wheels the builder left into ``target``. They were
        written by code nobody trusts, so what is taken is a plain file
        with a wheel's name, read as itself: a link is not followed,
        and whatever else lies there is left."""
        shutil.rmtree(target, ignore_errors=True)
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        try:
            target.mkdir(parents=True)
            names = sorted(name for name in os.listdir(source)
                           if WHEEL_RE.match(name))
            for name in names:
                if Path(source, name).is_symlink():
                    return [f"dependency installation failed: {name} is "
                            f"not a file"]
                with os.fdopen(os.open(Path(source, name), flags), "rb") as wheel:
                    if not stat.S_ISREG(os.fstat(wheel.fileno()).st_mode):
                        return [f"dependency installation failed: {name} "
                                f"is not a file"]
                    with open(target / name, "wb") as copy:
                        shutil.copyfileobj(wheel, copy)
        except OSError as exc:
            return [f"dependency installation failed: what was built "
                    f"could not be read: {exc}"]
        if not names:
            return ["dependency installation failed: the build left nothing "
                    "to install"]
        return []

    # ------------------------------------------------------------------
    def _run(self, argv: Sequence[str], timeout: int) -> tuple:
        if AgentEnvironment.runner is not None:
            return AgentEnvironment.runner(argv)
        # The venv must answer for itself: the host's import path leaking
        # in would make a broken environment look whole here and fail in
        # the worker, far from the cause.
        environment = {
            key: value for key, value in os.environ.items()
            if key not in ("PYTHONPATH", "PYTHONHOME")
        }
        try:
            completed = subprocess.run(
                list(argv), capture_output=True, text=True,
                timeout=timeout, env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return 1, str(exc)
        return completed.returncode, (completed.stdout or "") + (completed.stderr or "")
