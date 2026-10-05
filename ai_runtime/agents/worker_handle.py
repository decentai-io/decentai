"""The host side of docs/reference/worker-protocol.md.

One class, two ways in:

- a ``WorkerHandle`` instance — the long-lived client an execution
  layer holds: spawns the worker from its private venv, handshakes,
  keeps any number of invocations in flight, routes the worker's asks
  (resources, llm) to whoever may answer them, and owns the process's
  death.
- ``WorkerHandle.probe`` — installation's verification station: spawn,
  hello, shutdown, synchronously. What the handshake refuses is a
  broken package, named at install time in the exact environment the
  agent will really run in.

The spawn line is the same for both, and ``-I`` is not decoration:
isolated mode ignores PYTHONPATH and never prepends the working
directory, so the venv answers for itself — without it, a runtime
started from its own source tree would quietly hand every worker the
host's packages and the isolation would be theatre.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ai_runtime.agents.confinement import WorkerPlace
from ai_runtime.agents.events import Events
from ai_runtime.agents.spawner import Spawner
from ai_runtime.runtime_logging import RuntimeLoggerFactory
from decentai_sdk.manifest import Manifest


class WorkerError(RuntimeError):
    """The worker itself failed — a protocol error or a dead process.
    Not a function's failure; those are error RESULTS."""


class WorkerHandle:
    """One agent's worker process, from spawn to death."""

    PROTOCOL_VERSION = 1
    LINE_LIMIT = 2 * 1024 * 1024
    HANDSHAKE_TIMEOUT_SECONDS = 60
    GRACE_SECONDS = 5

    # ------------------------------------------------------------------
    # How a worker is spawned — one answer, both entry points
    # ------------------------------------------------------------------

    @staticmethod
    def _spawn_argv(python: str | Path) -> List[str]:
        return [str(python), "-I", "-m", "decentai_sdk.worker"]

    @staticmethod
    def _declared(manifest_document: dict) -> Tuple[str, dict]:
        """(the agent's name, where its manifest says it connects) —
        what a confined worker's pass at the proxy is written from."""
        manifest = Manifest(manifest_document or {})
        agent = (manifest_document or {}).get("agent") or {}
        return str(agent.get("name") or agent.get("id") or "an agent"), manifest.network

    #: What a package being verified may reach: nothing. Verification
    #: imports the code, and code that connects as it is imported is
    #: not waiting to be asked.
    NOTHING_DECLARED = {"declared": True, "any": False,
                        "hosts": [], "from_secrets": []}

    @staticmethod
    def _runs_from(python: str | Path, folder: str | Path) -> List[Path]:
        """What a worker runs from, and so must be able to read: its
        package, and the environment its interpreter lives in (the
        folder above the one the interpreter is in)."""
        return [Path(folder), Path(python).parent.parent]

    #: What a worker is given of this process's environment, by name:
    #: what a Python subprocess needs to run at all, and what an
    #: administrator set for every process on the machine. Nothing else —
    #: this process's own variables hold the model key and the
    #: backend's trust, and agent code is not this process. A variable
    #: not named here does not reach a worker, whatever else is set.
    PASSED_VARIABLES = frozenset((
        # the operating system's own: paths, temp, home, locale
        "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
        "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA",
        "APPDATA", "PROGRAMDATA", "LANG", "LANGUAGE", "TZ", "TERM",
        # trust and egress configured machine-wide
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "no_proxy",
        # the platform's own name for the way out, which confinement sets
        # at spawn; where nothing confines, whatever the deployment set
        "DECENTAI_PROXY",
        # the browsers Playwright installs, for agents that drive one
        "PLAYWRIGHT_BROWSERS_PATH",
    ))
    #: Families passed whole: locale settings, and the platform's knobs
    #: for agents — DECENTAI_BROWSER_*, DECENTAI_CODE_*, DECENTAI_WEB_*,
    #: and DECENTAI_AGENT_* for whatever a deployment wants its agents
    #: to read. Named family by family: other DECENTAI_ names are the
    #: platform's own (the runtime's user ids, an installer's password),
    #: and nothing of those is an agent's to see. The spool and the
    #: proxy address are set at spawn.
    PASSED_PREFIXES = ("LC_", "DECENTAI_BROWSER_", "DECENTAI_CODE_",
                       "DECENTAI_WEB_", "DECENTAI_AGENT_")

    @classmethod
    def _clean_environment(cls) -> Dict[str, str]:
        """The environment a worker starts with: the allow-list above
        out of this process's, and nothing else. PYTHONPATH and
        PYTHONHOME are never in it, so -I's guarantee holds even if a
        spawn path ever loses the flag.

        No bytecode: the worker runs from the store, whose folders are
        written once and named by what they hold — a __pycache__ beside
        the code is the one thing that would make them drift."""
        environment = {
            key: value for key, value in os.environ.items()
            if key in cls.PASSED_VARIABLES or key.startswith(cls.PASSED_PREFIXES)
        }
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return environment

    @classmethod
    def _hello_params(cls, folder: Path, manifest_document: dict) -> dict:
        return {
            "protocol_version": cls.PROTOCOL_VERSION,
            "folder": str(folder),
            "manifest": manifest_document,
        }

    def __init__(self, python: str | Path, folder: str | Path,
                 manifest_document: dict,
                 place: Optional[WorkerPlace] = None):
        self.python = Path(python)
        self.folder = Path(folder)
        self.manifest_document = manifest_document
        #: Where the worker is confined (confinement.py): a user, a home
        #: and a spool of its own. None where nothing confines, and the
        #: worker runs as this process's user.
        self.place = place
        #: The approved agent's ref, for what this worker does to be
        #: written down under; its place's name where it has one.
        self.agent_ref = ""
        #: async (method, params) -> result — answers the worker's asks
        #: (resources.*, llm.complete). An exception becomes the error
        #: response the SDK surfaces as ResourceDenied.
        self.router: Optional[Callable] = None
        #: async (call_id, description) -> None — progress notifications.
        self.progress: Optional[Callable] = None
        #: async (call_id, kind, params) -> None — a screen the function
        #: shows: kind is "frame" or "closed".
        self.screen: Optional[Callable] = None

        self.process: Optional[asyncio.subprocess.Process] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._serve_task: Optional[asyncio.Task] = None
        #: The worker's one death, once somebody has asked for it.
        self._death: Optional[asyncio.Future] = None
        #: A folder both sides can reach, for bytes too large for one
        #: line: a file read goes to the worker as a path in it, a file
        #: created comes back as one. Made at spawn, gone at death.
        self.spool: Optional[Path] = None
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    #: Base64 this long or shorter travels inline on the line as it
    #: always has; longer goes through the spool. A line is capped at
    #: LINE_LIMIT, and a scan or a signed form runs to many megabytes.
    INLINE_LIMIT = 256 * 1024

    def _spool_out(self, method: str, result: Any) -> Any:
        """A read too large for the line: the bytes are written to the
        spool and the worker is told where, instead of what."""
        if (method != "resources.read_file" or self.spool is None
                or not isinstance(result, dict)):
            return result
        encoded = result.get("content_base64")
        if not isinstance(encoded, str) or len(encoded) <= self.INLINE_LIMIT:
            return result
        target = self.spool / f"{uuid.uuid4().hex}.bin"
        target.write_bytes(base64.b64decode(encoded))
        return {**{k: v for k, v in result.items()
                   if k not in ("content_base64", "content")},
                "content_path": str(target)}

    #: The largest file a worker may hand over through the spool.
    SPOOL_MAX_BYTES = 256 * 1024 * 1024

    async def _spool_in(self, method: str, params: dict) -> dict:
        """A create whose bytes the worker left in the spool: read them
        back into the shape the router has always taken, and remove
        the file. Read on a thread — this loop serves every chat."""
        if method != "resources.create_file" or not params.get("content_path"):
            return params
        if self.spool is None:
            raise WorkerError("no spool is open for this worker")
        encoded = await asyncio.to_thread(
            self._spooled, str(params["content_path"]))
        return {**{k: v for k, v in params.items() if k != "content_path"},
                "content_base64": encoded}

    def _spooled(self, named: str) -> str:
        """The bytes of one file in the spool, encoded. The worker owns
        the spool and wrote the name, so nothing about it is trusted:
        only a plain file directly in the spool is read — a link is not
        followed (it could name any file this process can read), a pipe
        is not opened (it would wait for ever), and a file past the cap
        is refused rather than read."""
        target = self.spool / Path(named).name
        if Path(named).parent.resolve() != self.spool.resolve():
            raise WorkerError("content_path is not in this worker's spool")
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        try:
            if target.is_symlink():
                raise WorkerError("content_path is not a file")
            with os.fdopen(os.open(target, flags), "rb") as spooled:
                status = os.fstat(spooled.fileno())
                if not stat.S_ISREG(status.st_mode):
                    raise WorkerError("content_path is not a file")
                if status.st_size > self.SPOOL_MAX_BYTES:
                    raise WorkerError("the file is too large to store")
                data = spooled.read(self.SPOOL_MAX_BYTES + 1)
        except OSError as exc:
            raise WorkerError(f"the spooled file could not be read: {exc}")
        finally:
            try:
                target.unlink()
            except OSError:
                pass
        return base64.b64encode(data).decode("ascii")

    def whose(self) -> Dict[str, Any]:
        """Whose worker this is, as what it does is written down."""
        if self.place is not None:
            return self.place.whose()
        name, _ = self._declared(self.manifest_document)
        return {"agent": self.agent_ref, "name": name}

    # ------------------------------------------------------------------
    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> List[str]:
        """Spawn and handshake. Returns errors, empty when the worker is
        serving — the loader's old verdict, delivered by the worker."""
        argv = self._spawn_argv(self.python)
        environment = self._clean_environment()
        stop = whose = None
        if self.place is not None:
            errors = await asyncio.get_running_loop().run_in_executor(
                None, self.place.prepare)
            if errors:
                return errors
            self.spool = self.place.spool
            self.place.admit(*self._declared(self.manifest_document))
            argv = self.place.argv(argv, self._runs_from(self.python, self.folder))
            environment = self.place.environment(environment)
            # With whose it is, how what it keeps on disk is measured.
            stop = self.place.stop_line()
            whose = {**self.place.whose(), "measure": self.place.measure_line()}
        else:
            self.spool = Spawner.current.spool()
            environment["DECENTAI_SPOOL_DIR"] = str(self.spool)
        # Started where workers run (spawner.py): here, or in the
        # agents' own container, with its lines carried over.
        try:
            self.process = await Spawner.current.start(
                argv, environment, str(self.folder), self.LINE_LIMIT,
                stop=stop, whose=whose)
        except (OSError, asyncio.TimeoutError) as exc:
            Events.record("worker.failed", **self.whose(), why=str(exc))
            return [f"the worker could not be started: {exc}"]
        self._serve_task = asyncio.get_running_loop().create_task(self._serve())
        asyncio.get_running_loop().create_task(self._drain_stderr())

        try:
            await asyncio.wait_for(
                self._request(
                    "hello",
                    self._hello_params(self.folder, self.manifest_document),
                ),
                self.HANDSHAKE_TIMEOUT_SECONDS,
            )
        except WorkerError as exc:
            await self.kill(f"it was refused at its greeting: {exc}")
            return [str(exc)]
        except asyncio.TimeoutError:
            await self.kill("it did not answer its greeting")
            return ["the worker did not answer the handshake"]
        Events.record(
            "worker.started", **self.whose(),
            confined=self.place is not None,
            where="the agents' container" if Spawner.current.remote else "here")
        return []

    async def invoke(self, call_id: str, function: str,
                     inputs: dict, conversation: str = "") -> Tuple[dict, str]:
        """One invocation; any number may be in flight. Returns the
        function's ``(result, status)``; raises WorkerError when the
        WORKER is broken rather than the function. ``conversation`` is
        the chat's opaque key, so a function may keep state per chat."""
        params = {"call_id": call_id, "function": function, "inputs": inputs}
        if conversation:
            params["conversation"] = conversation
        answer = await self._request("invoke", params)
        return dict(answer.get("result") or {}), str(answer.get("status") or "error")

    async def cancel(self, call_id: str) -> bool:
        answer = await self._request("cancel", {"call_id": call_id})
        return bool(answer.get("cancelled"))

    async def stop(self) -> None:
        """Shutdown, politely then not. Safe to call twice."""
        if self.alive:
            try:
                await asyncio.wait_for(
                    self._request("shutdown", {}), self.GRACE_SECONDS
                )
            except (WorkerError, asyncio.TimeoutError):
                pass
        await self.kill("it was asked to leave")

    async def kill(self, reason: str = "the worker is gone") -> None:
        """End the worker. It dies once: whoever asks again — the reader
        that saw the pipe close, beside the caller that closed it —
        waits for that one death. A confined worker's end reaches
        everything its user runs, so a second one arriving late would
        land on the worker started after it."""
        if self._death is None:
            self._death = asyncio.ensure_future(self._die(reason))
        await asyncio.shield(self._death)

    async def _die(self, reason: str) -> None:
        # A confined worker's user is ended whether or not the worker
        # itself already went: what it started — a browser and its
        # driver — runs as that user and would outlive it.
        if self.place is not None or (
                self.process is not None and self.process.returncode is None):
            await self._end()
        if self.process is not None:
            await self.process.wait()
        spool, self.spool = self.spool, None
        if self.place is not None:
            self.place.dismiss()
            await asyncio.get_running_loop().run_in_executor(
                None, self.place.clear)
        elif spool is not None:
            shutil.rmtree(spool, ignore_errors=True)
        Events.record(
            "worker.ended", **self.whose(), why=reason,
            code=self.process.returncode if self.process is not None else None)
        # Last: a caller woken by this may start the next worker in the
        # same place at once, and the clearing above must be behind it.
        self._fail_pending(reason)

    async def _end(self) -> None:
        """End the process. A confined worker is another user's, which
        this process may not signal: the helper ends it, and everything
        else that user runs with it."""
        if self.place is not None:
            errors = await asyncio.get_running_loop().run_in_executor(
                None, self.place.stop)
            if errors:
                self.logger.error("; ".join(errors))
            return
        if self.process is None or self.process.returncode is not None:
            return
        try:
            self.process.kill()
        except ProcessLookupError:
            pass

    # ------------------------------------------------------------------
    async def _request(self, method: str, params: dict) -> Any:
        if not self.alive:
            raise WorkerError("the worker is not running")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send({"id": request_id, "method": method, "params": params})
        try:
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict) -> None:
        """A notification to the worker — no reply expected. The
        person's input on a screen travels this way."""
        if not self.alive:
            return
        await self._send({"method": method, "params": params})

    async def _send(self, message: dict) -> None:
        payload = json.dumps(message, separators=(",", ":")) + "\n"
        try:
            self.process.stdin.write(payload.encode("utf-8"))
            await self.process.stdin.drain()
        except (ConnectionError, RuntimeError, ValueError, AttributeError) as exc:
            # A dead pipe under a write — the worker (or its loop) is
            # gone; the caller hears one kind of failure, not asyncio's
            # internals.
            raise WorkerError(f"the worker's pipe is closed: {exc}")

    # ------------------------------------------------------------------
    async def _serve(self) -> None:
        """Everything the worker says, until it says nothing more — or
        breaks framing. Either way the process is dead when this
        returns: a worker nobody is reading must not stay `alive`, or
        the pool would hand it the next invocation to hold until its
        timeout."""
        reason = "the worker died"
        while True:
            try:
                line = await self.process.stdout.readline()
            except (ValueError, ConnectionError) as exc:
                reason = f"the worker broke the protocol: {exc}"
                break
            if not line:
                break
            try:
                message = json.loads(line)
            except ValueError:
                reason = "the worker sent a line that is not JSON"
                break
            if not isinstance(message, dict):
                reason = "the worker sent a line that is not a message"
                break
            self._route(message)
        # Ended where it runs, for using more than agents are given:
        # the calls it was serving are told that, in those words.
        reason = getattr(self.process, "ended_because", "") or reason
        if reason != "the worker died":
            self.logger.error(reason)
        await self.kill(reason)

    def _route(self, message: dict) -> None:
        if "method" in message and "id" in message:
            asyncio.get_running_loop().create_task(self._serve_ask(message))
        elif "id" in message:
            future = self._pending.get(message.get("id"))
            if future is None or future.done():
                return
            if "error" in message:
                detail = (message.get("error") or {}).get("message") or "refused"
                future.set_exception(WorkerError(detail))
            else:
                future.set_result(message.get("result"))
        elif message.get("method") == "progress" and self.progress is not None:
            params = message.get("params") or {}
            asyncio.get_running_loop().create_task(
                self.progress(str(params.get("call_id") or ""),
                              str(params.get("description") or ""))
            )
        elif message.get("method") in ("screen.frame", "screen.closed") \
                and self.screen is not None:
            params = dict(message.get("params") or {})
            call_id = str(params.pop("call_id", "") or "")
            kind = "frame" if message["method"] == "screen.frame" else "closed"
            asyncio.get_running_loop().create_task(
                self.screen(call_id, kind, params))

    async def _serve_ask(self, message: dict) -> None:
        """One worker ask (resources.*, llm.complete), answered by the
        router. No router, or a router refusal, is an error response —
        which the SDK surfaces in the function as ResourceDenied."""
        request_id = message["id"]
        if self.router is None:
            return await self._send({"id": request_id, "error": {
                "message": "nothing is serving resource asks here"}})
        method = str(message.get("method") or "")
        try:
            result = self._spool_out(method, await self.router(
                method,
                await self._spool_in(method, message.get("params") or {})
            ))
        except Exception as exc:
            return await self._send({"id": request_id,
                                     "error": {"message": str(exc) or "refused"}})
        await self._send({"id": request_id, "result": result})

    async def _drain_stderr(self) -> None:
        while True:
            line = await self.process.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                self.logger.log(logging.DEBUG, f"[worker] {text}")
                Events.record("log", **self.whose(), line=text)

    def _fail_pending(self, reason: str) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(WorkerError(reason))
        self._pending.clear()

    # ------------------------------------------------------------------
    # Installation's verification — no instance, no event loop
    # ------------------------------------------------------------------

    @classmethod
    def probe(cls, python: str | Path, folder: str | Path,
              manifest_document: dict, timeout: int = 120,
              place: Optional[WorkerPlace] = None) -> List[str]:
        """Spawn from the env, hello, shutdown. Returns errors, empty
        when the package verified.

        Synchronous on purpose — install() is synchronous, and a
        one-shot exchange needs no event loop. A worker that outlasts
        ``timeout`` is ended, so a refusal can never become a hang.

        Verification imports the package, so it is confined like any
        other run of it: ``place`` is installation's own, shared by
        every verification and held by one at a time."""
        if place is None:
            return cls._probe(python, folder, manifest_document, timeout, None)
        with place.confinement.verification_lock:
            errors = place.prepare()
            if errors:
                return errors
            agent_name, _ = cls._declared(manifest_document)
            place.admit(agent_name, cls.NOTHING_DECLARED)
            try:
                return cls._probe(
                    python, folder, manifest_document, timeout, place)
            finally:
                place.dismiss()
                place.stop()
                place.clear()

    @classmethod
    def _probe(cls, python: str | Path, folder: str | Path,
               manifest_document: dict, timeout: int,
               place: Optional[WorkerPlace]) -> List[str]:
        argv = cls._spawn_argv(python)
        environment = cls._clean_environment()
        # The whole exchange, written ahead: a worker answers its
        # greeting, then the order to leave, and is gone. One program
        # run to its end, wherever workers run (spawner.py).
        greeting = "".join(json.dumps(request) + "\n" for request in (
            {"id": 1, "method": "hello",
             "params": cls._hello_params(Path(folder), manifest_document)},
            {"id": 2, "method": "shutdown", "params": {}},
        ))
        if place is not None:
            # A confined worker is another user's: its place runs it,
            # and ends it when it outlasts its time.
            code, said = place.run(
                argv, reads=cls._runs_from(python, folder),
                environment=environment, timeout=timeout, feed=greeting,
                errors=Spawner.DROP, cwd=str(folder))
        else:
            code, said = Spawner.current.run(
                argv, environment=environment, cwd=str(folder),
                timeout=timeout, feed=greeting, errors=Spawner.DROP)
        if said.startswith(Spawner.COULD_NOT_RUN):
            return [f"worker could not be spawned: {said}"]
        answers = said.splitlines()
        try:
            reply = json.loads(answers[0])
        except (IndexError, ValueError):
            reply = None
        if not isinstance(reply, dict):
            return ["worker verification failed: "
                    + (said.strip()[-300:] or "the worker died during verification")]
        if "error" in reply:
            return [str((reply.get("error") or {}).get("message") or "refused")]
        if code != 0 or len(answers) < 2:
            return ["worker verification failed: "
                    + (said.strip()[-300:] if code != 0
                       else "the worker died during verification")]
        return []


