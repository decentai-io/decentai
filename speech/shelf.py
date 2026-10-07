"""The models on disk: which are there, which are on their way, and the
fetching of one that is asked for.

A model is fetched whole or not at all. Each file is written beside its
place under another name, its digest taken as it arrives, and moved
into place only when the digest is the catalogue's; a folder is marked
complete only when every file in it is. A fetch that was cut — the
network, a restart — leaves nothing that is mistaken for a model, and
the next one starts the file over.

It runs no model (engines.py) and decides nothing about who may ask
(the backend does).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, Optional

from speech.catalogue import NEEDS, PUBLISHED_AT, Model, ModelFile

logger = logging.getLogger("speech.shelf")

ABSENT, FETCHING, READY, FAILED = "absent", "fetching", "ready", "failed"

#: (address) -> the file's bytes, in pieces. The seam a test stands a
#: stub in; the real one is over HTTPS, through the machine's proxy
#: where it has one.
Opener = Callable[[str], Iterator[bytes]]


def over_https(address: str) -> Iterator[bytes]:
    import httpx

    with httpx.stream("GET", address, follow_redirects=True,
                      timeout=httpx.Timeout(30.0, read=120.0)) as reply:
        reply.raise_for_status()
        yield from reply.iter_bytes(1024 * 1024)


class Shelf:
    #: the file that says a model's folder holds all of it
    COMPLETE = ".complete"

    def __init__(self, directory: str | Path,
                 opener: Opener = over_https, host: str = ""):
        self.directory = Path(directory)
        self.opener = opener
        self.host = (host or os.environ.get("SPEECH_MODEL_HOST")
                     or PUBLISHED_AT)
        self._lock = threading.Lock()
        #: what -> {"state", "bytes", "of", "error"}, for what was asked
        #: for since this process started
        self._told: Dict[str, dict] = {}
        self._fetching: Dict[str, threading.Thread] = {}

    # ------------------------------------------------------------------
    def folder(self, model: Model) -> Path:
        return self.directory / model.key

    def has(self, model: Model) -> bool:
        return (self.folder(model) / self.COMPLETE).is_file()

    def ready(self, what: str) -> bool:
        return all(self.has(model) for model in NEEDS[what])

    def status(self, what: str) -> dict:
        """Where one of the two stands: ready, on its way and how far,
        failed and why, or not asked for."""
        total = sum(model.size for model in NEEDS[what])
        if self.ready(what):
            return {"state": READY, "bytes": total, "of": total, "error": ""}
        with self._lock:
            told = dict(self._told.get(what) or {})
        if not told:
            return {"state": ABSENT, "bytes": 0, "of": total, "error": ""}
        return {"state": told["state"], "bytes": told["bytes"],
                "of": total, "error": told.get("error", "")}

    # ------------------------------------------------------------------
    def prepare(self, what: str) -> dict:
        """Start fetching what one of the two needs, unless it is there
        or already on its way. Answers at once with where it stands."""
        if what not in NEEDS:
            raise KeyError(what)
        with self._lock:
            running = self._fetching.get(what)
            if self.ready(what) or (running and running.is_alive()):
                pass
            else:
                self._told[what] = {"state": FETCHING, "bytes": 0, "error": ""}
                thread = threading.Thread(
                    target=self._fetch, args=(what,), daemon=True,
                    name=f"fetch-{what}")
                self._fetching[what] = thread
                thread.start()
        return self.status(what)

    def wait(self, what: str, timeout: Optional[float] = None) -> None:
        thread = self._fetching.get(what)
        if thread is not None:
            thread.join(timeout)

    def _fetch(self, what: str) -> None:
        try:
            done = 0
            for model in NEEDS[what]:
                if self.has(model):
                    done += model.size
                    continue
                self._fetch_model(model, done, what)
                done += model.size
            with self._lock:
                self._told[what] = {"state": READY, "bytes": done, "error": ""}
            logger.info("%s is ready", what)
        except Exception as exc:
            logger.warning("fetching %s failed: %s", what, exc)
            with self._lock:
                self._told[what] = {
                    "state": FAILED,
                    "bytes": (self._told.get(what) or {}).get("bytes", 0),
                    "error": str(exc) or exc.__class__.__name__}

    def _fetch_model(self, model: Model, before: int, what: str) -> None:
        folder = self.folder(model)
        folder.mkdir(parents=True, exist_ok=True)
        done = before
        for file in model.files:
            target = folder / file.name
            if not self._is(target, file):
                self._fetch_file(model, file, target, done, what)
            done += file.size
            self._tell(what, done)
        (folder / self.COMPLETE).write_text(model.revision, encoding="utf-8")

    def _fetch_file(self, model: Model, file: ModelFile, target: Path,
                    before: int, what: str) -> None:
        arriving = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        size = 0
        logger.info("fetching %s/%s (%d bytes)", model.key, file.name, file.size)
        try:
            with arriving.open("wb") as out:
                for piece in self.opener(model.address(file, self.host)):
                    size += len(piece)
                    if size > file.size:
                        raise ValueError(
                            f"{file.name} is larger than the {file.size} "
                            f"bytes it was published as")
                    digest.update(piece)
                    out.write(piece)
                    self._tell(what, before + size)
            if size != file.size or digest.hexdigest() != file.sha256:
                raise ValueError(
                    f"{file.name} is not the file that was published: "
                    f"its bytes do not match the digest kept for it")
            arriving.replace(target)
        finally:
            arriving.unlink(missing_ok=True)

    @staticmethod
    def _is(target: Path, file: ModelFile) -> bool:
        """A file already in place from a fetch that was cut after it:
        kept when it is the one, digest and all."""
        if not target.is_file() or target.stat().st_size != file.size:
            return False
        digest = hashlib.sha256()
        with target.open("rb") as held:
            for piece in iter(lambda: held.read(1024 * 1024), b""):
                digest.update(piece)
        return digest.hexdigest() == file.sha256

    def _tell(self, what: str, done: int) -> None:
        with self._lock:
            told = self._told.setdefault(
                what, {"state": FETCHING, "bytes": 0, "error": ""})
            told["bytes"] = done


def asked_for_at_start(setting: Optional[str]) -> Iterable[str]:
    """What SPEECH_FETCH names: fetched as the container starts, with
    nobody having asked. Speech to text where it says nothing, since
    that is on in a new organization; nothing where it is empty."""
    if setting is None:
        return ("transcription",)
    return tuple(word for word in
                 (part.strip().lower() for part in setting.split(","))
                 if word in NEEDS)
