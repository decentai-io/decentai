"""The host side of docs/reference/worker-protocol.md.

One class, two ways in:

- a ``WorkerHandle`` instance — the long-lived client an execution
  layer holds: has an Execution Sandbox start the worker from its
  private venv there (agents/sandbox.py), handshakes, keeps any number of
  invocations in flight, routes the worker's asks (pool.py names them)
  to whoever may answer them, and owns the process's death.
- ``WorkerHandle.probe`` — installation's verification station: start,
  hello, shutdown, in the place that is nobody's agent. What the
  handshake refuses is a broken package, named at install time in the
  exact environment the agent will really run in.

The sandbox starts the worker as the SDK's worker program, from the
environment's own interpreter, isolated: the venv answers for itself.
A file too large for one line travels in pieces on the same wire,
ahead of the message that names it, in either direction: nothing is
shared between the runtime and the sandbox to leave it in.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ai_runtime.agents.confinement import WorkerPlace
from sandbox.events import Events
from decentai_sdk.manifest import Manifest


class WorkerError(RuntimeError):
    """The worker itself failed — a protocol error or a dead process.
    Not a function's failure; those are error RESULTS."""


class WorkerHandle:
    """One agent's worker process, from start to death."""

    PROTOCOL_VERSION = 2
    LINE_LIMIT = 2 * 1024 * 1024
    HANDSHAKE_TIMEOUT_SECONDS = 60
    GRACE_SECONDS = 5

    #: What a package being verified may reach: nothing. Verification
    #: imports the code, and code that connects as it is imported is
    #: not waiting to be asked.
    NOTHING_DECLARED = {"declared": True, "any": False,
                        "hosts": [], "from_secrets": []}

    #: What a worker is given of this process's environment, by name:
    #: what a Python subprocess needs to run at all, and what an
    #: administrator set for every process on the machine. Nothing else —
    #: this process's own variables hold the model key and the
    #: Data Core's trust, and agent code is not this process. A variable
    #: not named here does not reach a worker, whatever else is set.
    PASSED_VARIABLES = frozenset((
        # the operating system's own: paths, temp, home, locale
        "PATH", "TEMP", "TMP", "TMPDIR", "HOME", "LANG", "LANGUAGE", "TZ",
        "TERM",
        # trust and egress configured machine-wide
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "no_proxy",
        # the platform's own name for the way out; the place sets it
        # at spawn
        "DECENTAI_PROXY",
        # the browsers Playwright installs, for agents that drive one
        "PLAYWRIGHT_BROWSERS_PATH",
    ))
    #: Families passed whole: locale settings, and the platform's knobs
    #: for agents — DECENTAI_BROWSER_*, DECENTAI_CODE_*, DECENTAI_WEB_*,
    #: and DECENTAI_AGENT_* for whatever a deployment wants its agents
    #: to read. Named family by family: other DECENTAI_ names are the
    #: platform's own (the runtime's user ids, an installer's password),
    #: and nothing of those is an agent's to see. The proxy address is
    #: set at spawn.
    PASSED_PREFIXES = ("LC_", "DECENTAI_BROWSER_", "DECENTAI_CODE_",
                       "DECENTAI_WEB_", "DECENTAI_AGENT_")

    def __init__(self, python: str | Path, folder: str | Path,
                 manifest_document: dict, place: WorkerPlace):
        self.python = Path(python)
        self.folder = Path(folder)
        self.manifest_document = manifest_document
        #: Where the worker is confined (confinement.py): its sandbox, a
        #: user and a home of its own there, and its pass at the proxy.
        self.place = place
        #: async (method, params) -> result — answers the worker's asks
        #: (the pool's router). An exception becomes the error
        #: response the SDK surfaces as ResourceDenied.
        self.router: Optional[Callable] = None
        #: async (call_id, description) -> None — progress notifications.
        self.progress: Optional[Callable] = None
        #: async (call_id, kind, params) -> None — a screen the function
        #: shows: kind is "frame" or "closed".
        self.screen: Optional[Callable] = None

        self.process: Optional[Any] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._serve_task: Optional[asyncio.Task] = None
        #: The tasks started for this worker — an ask being answered,
        #: a progress line, a screen's frame, its log drained — held
        #: until each is done. A loop keeps a task only weakly, and
        #: one nobody holds can be collected mid-ask: its answer never
        #: sent, and the call waiting out its whole clock.
        self._tasks: set = set()
        #: The worker's one death, once somebody has asked for it.
        self._death: Optional[asyncio.Future] = None
        #: Whether it was asked to leave: its leaving is then that, even
        #: when the reader sees it go before the asking finishes.
        self._leaving = False
        #: The pieces of large files the worker sent, by stream, until
        #: the request that names them: None for one that was more than
        #: a file may be, or not base64.
        self._streams: Dict[str, Optional[bytearray]] = {}
        self._streamed = 0
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # What a worker is started with — both entry points
    # ------------------------------------------------------------------

    @staticmethod
    def _declared(manifest_document: dict) -> Tuple[str, dict]:
        """(the agent's name, where its manifest says it connects) —
        what a confined worker's pass at the proxy is written from."""
        manifest = Manifest(manifest_document or {})
        agent = (manifest_document or {}).get("agent") or {}
        return str(agent.get("name") or agent.get("id") or "an agent"), manifest.network

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

    # ------------------------------------------------------------------
    # Large files, in pieces on the wire
    # ------------------------------------------------------------------

    #: Base64 this long or shorter travels inline on the line; longer
    #: travels as pieces of this length, each a ``bytes`` notification,
    #: ahead of the message that names them. A line is capped at
    #: LINE_LIMIT, and a scan or a signed form runs to many megabytes.
    INLINE_LIMIT = 256 * 1024
    #: The largest file a worker may hand over, and the most it may
    #: have sent in pieces that no request has named yet.
    FILE_MAX_BYTES = 256 * 1024 * 1024

    async def _stream_out(self, method: str, result: Any) -> Any:
        """A read too large for the line: its bytes are sent in pieces,
        and the answer names them instead of holding them."""
        if method != "resources.read_file" or not isinstance(result, dict):
            return result
        encoded = result.get("content_base64")
        if not isinstance(encoded, str) or len(encoded) <= self.INLINE_LIMIT:
            return result
        stream = uuid.uuid4().hex
        for begin in range(0, len(encoded), self.INLINE_LIMIT):
            await self._send({"method": "bytes", "params": {
                "stream": stream,
                "data": encoded[begin:begin + self.INLINE_LIMIT]}})
        return {**{k: v for k, v in result.items()
                   if k not in ("content_base64", "content")},
                "content_stream": stream}

    def _receive_piece(self, params: Any) -> None:
        """One piece of a file the worker is sending. Nothing about it
        is trusted: a piece that is not base64, or one past what a file
        may be, spoils its stream, and the request that names it is
        refused."""
        if not isinstance(params, dict):
            return
        stream = str(params.get("stream") or "")[:64]
        piece = params.get("data")
        if not stream or not isinstance(piece, str):
            return
        if stream not in self._streams and len(self._streams) >= 16:
            return                       # more open at once than any create needs
        kept = self._streams.setdefault(stream, bytearray())
        if kept is None:
            return
        try:
            data = base64.b64decode(piece, validate=True)
        except (ValueError, binascii.Error):
            data = None
        if data is None or self._streamed + len(data) > self.FILE_MAX_BYTES:
            self._streamed -= len(kept)
            self._streams[stream] = None
            return
        kept.extend(data)
        self._streamed += len(data)

    async def _stream_in(self, method: str, params: dict) -> dict:
        """A create whose bytes the worker sent in pieces: the pieces,
        joined, in the shape the router takes."""
        if method != "resources.create_file" or not params.get("content_stream"):
            return params
        stream = str(params["content_stream"])
        if stream not in self._streams:
            raise WorkerError("the file's pieces did not arrive")
        kept = self._streams.pop(stream)
        if kept is None:
            raise WorkerError("the file did not arrive whole: it was larger "
                              "than a file may be, or not base64")
        self._streamed -= len(kept)
        # Encoded off the loop: a file of up to FILE_MAX_BYTES, and this
        # loop serves every chat.
        encoded = await asyncio.to_thread(
            lambda: base64.b64encode(bytes(kept)).decode("ascii"))
        return {**{k: v for k, v in params.items() if k != "content_stream"},
                "content_base64": encoded}

    def whose(self) -> Dict[str, Any]:
        """Whose worker this is, as what it does is written down."""
        return self.place.whose()

    # ------------------------------------------------------------------
    # Its life
    # ------------------------------------------------------------------

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> List[str]:
        """Have the sandbox start the worker in the agent's place,
        and handshake. Returns errors, empty when the worker is
        serving: whether the package loads is the worker's to say, from
        the environment it runs in."""
        self.place.admit(*self._declared(self.manifest_document))
        try:
            self.process = await self._started()
        except (OSError, asyncio.TimeoutError) as exc:
            Events.record("worker.failed", **self.whose(), why=str(exc))
            # Nothing runs, so nothing will die and take its pass back.
            self._release()
            return [f"the worker could not be started: {exc}"]
        self._serve_task = asyncio.get_running_loop().create_task(self._serve())
        self._start(self._drain_stderr())

        try:
            await asyncio.wait_for(
                self._request(
                    "hello",
                    self._hello_params(self.folder, self.manifest_document),
                ),
                self.HANDSHAKE_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            # Whoever asked for this worker stopped waiting (a person
            # pressed stop on a slow first call). Nobody will be handed
            # it, so nobody would ever end it: it ends here.
            await asyncio.shield(self.kill("its start was cancelled"))
            raise
        except WorkerError as exc:
            await self.kill(f"it was refused at its greeting: {exc}")
            return [str(exc)]
        except asyncio.TimeoutError:
            await self.kill("it did not answer its greeting")
            return ["the worker did not answer the handshake"]
        Events.record("worker.started", **self.whose())
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
        # The worker's word, so its shape is checked: an answer that is
        # not a result is the worker's failure, said as one.
        result = answer.get("result") if isinstance(answer, dict) else None
        status = answer.get("status") if isinstance(answer, dict) else None
        if result is None:
            result = {}
        if not isinstance(result, dict) or status not in ("success", "error"):
            raise WorkerError(
                "the worker answered a call with something that is not a "
                "result")
        return dict(result), status

    async def cancel(self, call_id: str) -> bool:
        answer = await self._request("cancel", {"call_id": call_id})
        return bool(answer.get("cancelled"))

    async def stop(self) -> None:
        """Shutdown, politely then not. Safe to call twice."""
        self._leaving = True
        if self.alive:
            try:
                await asyncio.wait_for(
                    self._request("shutdown", {}), self.GRACE_SECONDS
                )
            except (WorkerError, asyncio.TimeoutError):
                pass
        await self.kill("it was asked to leave")

    async def stop_when_idle(self, seconds: float) -> None:
        """Shutdown once nothing is in flight on it — at most
        ``seconds`` from now: a worker retired from a sandbox that
        drains finishes what it is doing first."""
        loop = asyncio.get_running_loop()
        until = loop.time() + seconds
        while self.alive and self._pending and loop.time() < until:
            await asyncio.sleep(0.2)
        await self.stop()

    async def kill(self, reason: str = "the worker is gone") -> None:
        """End the worker. It dies once: whoever asks again — the reader
        that saw the pipe close, beside the caller that closed it —
        waits for that one death. The worker's end reaches everything
        its user runs, so a second one arriving late would land on the
        worker started after it."""
        if self._death is None:
            self._death = asyncio.ensure_future(self._die(reason))
        await asyncio.shield(self._death)

    async def _die(self, reason: str) -> None:
        # The worker's user is ended whether or not the worker itself
        # already went: what it started — a browser and its driver —
        # runs as that user and would outlive it.
        await self._end()
        if self.process is not None:
            await self.process.wait()
        self._release()
        Events.record(
            "worker.ended", **self.whose(), why=reason,
            code=self.process.returncode if self.process is not None else None)
        # Last: a caller woken by this may start the next worker in the
        # same place at once, and the clearing above must be behind it.
        self._fail_pending(reason)

    async def _started(self):
        """The worker's process, started where it runs: the agent's own
        place in its Execution Sandbox, from its environment there, in
        its package, pointed at the proxy with its pass."""
        return await self.place.container.start_worker(
            agent=self.place.name, name=self.place.label,
            python=self.python, package=self.folder,
            environment=self._clean_environment(),
            proxy=self.place.proxy_address(), limit=self.LINE_LIMIT)

    def _release(self) -> None:
        """Take back the worker's pass at the proxy, and what it sent of
        a file nobody named. Its place is the sandbox's to empty, which
        it does when the worker has gone."""
        self._streams.clear()
        self._streamed = 0
        self.place.dismiss()

    async def _end(self) -> None:
        """End the process, where it runs: the sandbox ends every
        process of the agent's user — the worker, and whatever it
        started with it."""
        errors = await self.place.container.stop(self.place.name)
        if errors:
            self.logger.error("; ".join(errors))

    # ------------------------------------------------------------------
    # The wire
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
            try:
                self._route(message)
            except Exception as exc:
                # JSON, and a mapping, and still not a message ("id" a
                # list, "params" a word). The reader must not end and
                # leave the worker alive and unread.
                reason = ("the worker sent a message of the wrong shape "
                          f"({type(exc).__name__})")
                break
        # Ended where it runs, for using more than agents are given:
        # the calls it was serving are told that, in those words.
        reason = getattr(self.process, "ended_because", "") or reason
        # Asked to leave, it left: its answer and its going may arrive
        # together, and the reader then sees the going first.
        if reason == "the worker died" and self._leaving:
            reason = "it was asked to leave"
        if reason != "the worker died":
            self.logger.error(reason)
        await self.kill(reason)

    def _start(self, work) -> None:
        """One task of this worker's, held until it is done."""
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _route(self, message: dict) -> None:
        if "method" in message and "id" in message:
            self._start(self._serve_ask(message))
        elif "id" in message:
            future = self._pending.get(message.get("id"))
            if future is None or future.done():
                return
            if "error" in message:
                detail = (message.get("error") or {}).get("message") or "refused"
                future.set_exception(WorkerError(detail))
            else:
                future.set_result(message.get("result"))
        elif message.get("method") == "bytes":
            self._receive_piece(message.get("params"))
        elif message.get("method") == "progress" and self.progress is not None:
            params = message.get("params") or {}
            self._start(self.progress(str(params.get("call_id") or ""),
                                      str(params.get("description") or "")))
        elif message.get("method") in ("screen.frame", "screen.closed") \
                and self.screen is not None:
            params = dict(message.get("params") or {})
            call_id = str(params.pop("call_id", "") or "")
            kind = "frame" if message["method"] == "screen.frame" else "closed"
            self._start(self.screen(call_id, kind, params))

    async def _serve_ask(self, message: dict) -> None:
        """One worker ask, answered by the router. No router, or a router
        refusal, is an error response — which the SDK surfaces in the
        function as ResourceDenied."""
        request_id = message["id"]
        if self.router is None:
            return await self._send({"id": request_id, "error": {
                "message": "nothing is serving resource asks here"}})
        method = str(message.get("method") or "")
        try:
            result = await self._stream_out(method, await self.router(
                method,
                await self._stream_in(method, message.get("params") or {})
            ))
        except Exception as exc:
            return await self._send({"id": request_id,
                                     "error": {"message": str(exc) or "refused"}})
        try:
            await self._send({"id": request_id, "result": result})
        except (TypeError, ValueError) as exc:
            # An answer JSON cannot say. The ask is answered all the
            # same, or the function waits out its whole timeout.
            await self._send({"id": request_id, "error": {"message": (
                f"the platform's answer could not be sent "
                f"({type(exc).__name__})")}})

    async def _drain_stderr(self) -> None:
        while True:
            try:
                line = await self.process.stderr.readline()
            except ValueError:
                # A line longer than the reader takes. What was read of
                # it is dropped, and the log goes on after it.
                Events.record("log", **self.whose(),
                              line="(a line too long to keep was dropped)")
                continue
            except (ConnectionError, OSError):
                return
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                self.logger.debug(f"[worker] {text}")
                Events.record("log", **self.whose(), line=text)

    def _fail_pending(self, reason: str) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(WorkerError(reason))
        self._pending.clear()

    # ------------------------------------------------------------------
    # Installation's verification — no instance
    # ------------------------------------------------------------------

    @classmethod
    async def probe(cls, python: str | Path, folder: str | Path,
                    manifest_document: dict, place: WorkerPlace,
                    timeout: int = 120) -> List[str]:
        """Start a worker from the environment, hello, shutdown — in
        installation's own place, which is nobody's agent and which the
        sandbox lends to one verification at a time. Returns errors,
        empty when the package verified. A worker that outlasts
        ``timeout`` is ended, so a refusal can never become a hang."""
        agent_name, _ = cls._declared(manifest_document)
        place.admit(agent_name, cls.NOTHING_DECLARED)
        handle = cls(python, folder, manifest_document, place)
        try:
            return await asyncio.wait_for(handle._probe(), timeout)
        except asyncio.TimeoutError:
            return [f"worker verification failed: the worker did not "
                    f"answer within {timeout} seconds"]
        finally:
            if handle.process is not None:
                await handle.kill("verification is over")
            else:
                handle._release()

    async def _probe(self) -> List[str]:
        """The whole exchange: the worker answers its greeting, then
        the order to leave, and is gone."""
        try:
            self.process = await self._started()
        except (OSError, asyncio.TimeoutError) as exc:
            return [f"worker could not be spawned: {exc}"]
        self._serve_task = asyncio.get_running_loop().create_task(self._serve())
        self._start(self._drain_stderr())
        Events.record("worker.started", **self.whose())
        try:
            await self._request(
                "hello", self._hello_params(self.folder, self.manifest_document))
            await self._request("shutdown", {})
        except WorkerError as exc:
            return [str(exc) or "refused"]
        return []

