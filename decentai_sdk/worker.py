"""The worker entry loop (docs/reference/worker-protocol.md, version 1).

One process, one package. The host spawns this module inside the
package's private venv, speaks newline-delimited JSON over stdin/stdout,
and keeps every decision to itself — this end imports the one agent,
verifies code against contract, and runs function bodies that can only
*ask* for things.

Framing faults are fatal on purpose: a line that is not JSON, or is too
long, means the two ends have lost each other, and a worker that cannot
trust its stream has nothing safe left to do but exit. EOF on stdin is
the host's death or its order to die — same response.

Stdlib only, like the rest of the SDK: this file runs where the
platform's packages do not exist.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import importlib.machinery
import importlib.util
import inspect
import json
import logging
import os
import queue
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from decentai_sdk.base import AgentBase, Completion, FunctionCall, ResourceDenied, Screen
from decentai_sdk.manifest import Manifest

PROTOCOL_VERSION = 1
#: One line of the wire, in either direction: what the runtime reads
#: from a worker (ai_runtime/agents/worker_handle.py LINE_LIMIT) the
#: worker reads from the runtime. Bytes above a quarter of it travel by
#: the spool, never by the line.
MAX_LINE_BYTES = 2 * 1024 * 1024
PACKAGE_PREFIX = "decentai_agents"

logger = logging.getLogger("decentai_sdk.worker")


class WireFault(Exception):
    """The stream can no longer be trusted; the worker exits."""


class Wire:
    """Newline-delimited JSON over this process's stdin/stdout.

    Blocking pipe I/O lives on two plain threads — stdin cannot be an
    asyncio stream on every platform — and the asyncio side sees a clean
    pair: ``await read()`` for the next message (None on EOF), ``send()``
    for any outgoing one. Writes are ordered by the queue; stdout is
    written by exactly one thread, so messages never interleave.
    """

    _CLOSE = object()

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._outgoing: queue.Queue = queue.Queue()
        self._drained = threading.Event()
        threading.Thread(target=self._read_forever, daemon=True).start()
        threading.Thread(target=self._write_forever, daemon=True).start()

    async def read(self) -> Optional[dict]:
        message = await self._incoming.get()
        if isinstance(message, WireFault):
            raise message
        return message

    def send(self, message: dict) -> None:
        """Serialized HERE, in the caller: a message that cannot be
        JSON (bytes handed where text was meant) raises where it was
        sent, as the function's own error. Serialized on the writer
        thread it killed the thread, and every later ask waited on a
        pipe nothing wrote to again."""
        self._outgoing.put(json.dumps(message, separators=(",", ":")) + "\n")

    def flush(self, timeout: float = 5.0) -> None:
        """Block until every queued message has reached the pipe. The
        last thing a worker does before exiting, so its final answer is
        never lost to teardown."""
        self._outgoing.put(self._CLOSE)
        self._drained.wait(timeout)

    # ------------------------------------------------------------------
    def _read_forever(self) -> None:
        stream = sys.stdin.buffer
        while True:
            line = stream.readline(MAX_LINE_BYTES + 1)
            if not line:
                self._deliver(None)  # EOF — the order to die
                return
            if len(line) > MAX_LINE_BYTES:
                self._deliver(WireFault("incoming line exceeds 2 MiB"))
                return
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self._deliver(WireFault("incoming line is not JSON"))
                return
            if not isinstance(message, dict):
                self._deliver(WireFault("incoming message is not an object"))
                return
            self._deliver(message)

    def _deliver(self, item: Any) -> None:
        self._loop.call_soon_threadsafe(self._incoming.put_nowait, item)

    def _write_forever(self) -> None:
        stream = sys.stdout.buffer
        while True:
            payload = self._outgoing.get()
            if payload is self._CLOSE:
                self._drained.set()
                return
            stream.write(payload.encode("utf-8"))
            stream.flush()


class WireResources:
    """The ``call.resources`` surface, backed by requests to the host.

    The same method signatures the platform's own ResourceAccess has
    (ai_runtime/execution/resources.py), so agent code and the test
    harness see one surface. Every ask carries the invocation's
    call_id — what it may do is the host's to decide, per call, against
    live grants. A host refusal surfaces as ResourceDenied, exactly as
    an in-process refusal does.
    """

    def __init__(self, rpc, call_id: str):
        self._rpc = rpc
        self._call_id = call_id

    async def _ask(self, method: str, **params: Any) -> Any:
        return await self._rpc(method, {"call_id": self._call_id, **params})

    async def use_secret(self, resource_id: str,
                         ref: Optional[str] = None) -> Dict[str, Any]:
        """The credential for a declared secret — the one the platform
        resolves for this slot, or, by ``ref``, one of several the
        person holds (``list_secrets``): a second mailbox, another
        site of the same service."""
        return await self._ask("resources.use_secret",
                               resource_id=resource_id, ref=ref)

    async def list_secrets(self, resource_id: str) -> List[Dict[str, Any]]:
        """Every credential this slot may use, plain half only — ref,
        name, the keys a person names it by (an account, a site) and
        whether it is the default — so a function can offer the choice
        or take an account as an input. Never a value."""
        return await self._ask("resources.list_secrets",
                               resource_id=resource_id)

    async def list_data(self, resource_id: str, filters: Optional[dict] = None):
        return await self._ask("resources.list_data",
                               resource_id=resource_id, filters=filters)

    async def read_data(self, resource_id: str, ref: str) -> Dict[str, Any]:
        return await self._ask("resources.read_data",
                               resource_id=resource_id, ref=ref)

    async def create_data(self, resource_id: str, fields: dict) -> Dict[str, Any]:
        return await self._ask("resources.create_data",
                               resource_id=resource_id, fields=fields)

    async def update_data(self, resource_id: str, ref: str, fields: dict) -> Dict[str, Any]:
        return await self._ask("resources.update_data",
                               resource_id=resource_id, ref=ref, fields=fields)

    async def delete_data(self, resource_id: str, ref: str) -> bool:
        answer = await self._ask("resources.delete_data",
                                 resource_id=resource_id, ref=ref)
        return bool((answer or {}).get("deleted"))

    async def list_files(self, resource_id: str):
        return await self._ask("resources.list_files", resource_id=resource_id)

    #: Base64 this long or shorter travels inline on the line; longer
    #: goes through the spool folder the host opened for this worker
    #: (DECENTAI_SPOOL_DIR). Agent code sees neither: a read always
    #: comes back with its bytes, a create always takes them.
    INLINE_LIMIT = 256 * 1024

    async def read_file(self, resource_id: str, ref: str) -> Dict[str, Any]:
        record = dict(await self._ask("resources.read_file",
                                      resource_id=resource_id, ref=ref) or {})
        path = record.pop("content_path", None)
        if path:
            # Too large for the line: the host left the bytes in the
            # spool and named the file. Read once, then it is gone.
            target = Path(str(path))
            try:
                data = target.read_bytes()
            finally:
                try:
                    target.unlink()
                except OSError:
                    pass
            record["content_base64"] = base64.b64encode(data).decode("ascii")
            try:
                record["content"] = data.decode("utf-8")
            except UnicodeDecodeError:
                record.pop("content", None)
        return record

    async def create_file(self, resource_id: str, filename: str,
                          content: Optional[str] = None,
                          content_base64: Optional[str] = None) -> Dict[str, Any]:
        """Text as ``content``, bytes as ``content_base64`` — one of the
        two. A read gives both back where they apply: ``content`` when
        the bytes are text, ``content_base64`` always."""
        spool = os.environ.get("DECENTAI_SPOOL_DIR") or ""
        encoded = content_base64
        if encoded is None and isinstance(content, (bytes, bytearray)):
            # Bytes handed as content: taken as the bytes they are. Sent
            # raw they could not be serialized, and the ask never went.
            encoded, content = base64.b64encode(bytes(content)).decode("ascii"), None
            content_base64 = encoded
        elif encoded is None and content is not None:
            encoded = base64.b64encode(
                str(content).encode("utf-8")).decode("ascii")
        if spool and encoded is not None and len(encoded) > self.INLINE_LIMIT:
            target = Path(spool) / f"{uuid.uuid4().hex}.bin"
            target.write_bytes(base64.b64decode(encoded))
            return await self._ask("resources.create_file",
                                   resource_id=resource_id, filename=filename,
                                   content_path=str(target))
        return await self._ask("resources.create_file", resource_id=resource_id,
                               filename=filename, content=content,
                               content_base64=content_base64)

    async def delete_file(self, resource_id: str, ref: str) -> bool:
        answer = await self._ask("resources.delete_file",
                                 resource_id=resource_id, ref=ref)
        return bool((answer or {}).get("deleted"))


class AgentWorker:
    """One run of the protocol: handshake, then invocations until told
    to stop or the stream ends."""

    def __init__(self, wire: Wire):
        self.wire = wire
        self.agent: Optional[AgentBase] = None
        #: call_id -> the running invocation task.
        self.invocations: Dict[str, asyncio.Task] = {}
        #: the screen each running call shows, by call id — the person's
        #: input is routed here and dropped once the call is over
        self.screens: Dict[str, Screen] = {}
        #: id -> future awaiting the host's answer to OUR request.
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._shutdown = asyncio.Event()

    # ------------------------------------------------------------------
    async def run(self) -> None:
        while not self._shutdown.is_set():
            try:
                message = await self.wire.read()
            except WireFault as fault:
                logger.error(f"wire fault: {fault}")
                break
            if message is None:
                break  # EOF
            self._dispatch(message)
        await self._close_agent()

    def _dispatch(self, message: dict) -> None:
        if not isinstance(message, dict):
            return  # JSON, and not a message: nothing to answer
        if "method" in message and "id" in message:
            self._serve(message)
        elif "id" in message:
            self._settle(message)
        elif message.get("method") == "screen.input":
            # The person acting on a screen a function is showing:
            # handed to that call's screen, or dropped when the call
            # has ended — there is nobody to act for.
            params = message.get("params")
            if not isinstance(params, dict):
                return
            screen = self.screens.get(str(params.get("call_id") or ""))
            events = params.get("events")
            if screen is not None and isinstance(events, list):
                screen.receive(list(events))

    # ------------------------------------------------------------------
    # Requests FROM the host
    # ------------------------------------------------------------------
    def _serve(self, message: dict) -> None:
        method = message.get("method")
        params = message.get("params") or {}
        request_id = message["id"]
        if not isinstance(params, dict):
            return self._refuse(request_id, "params must be an object")

        if method == "hello":
            self._answer(request_id, self._hello(params))
        elif method == "invoke":
            self._invoke(request_id, params)
        elif method == "cancel":
            self._cancel(request_id, params)
        elif method == "shutdown":
            self.wire.send({"id": request_id, "result": {}})
            self._shutdown.set()
        else:
            self._refuse(request_id, f"unknown method '{method}'")

    def _answer(self, request_id: Any, outcome: Any) -> None:
        if isinstance(outcome, str):
            self._refuse(request_id, outcome)
        else:
            self.wire.send({"id": request_id, "result": outcome})

    def _refuse(self, request_id: Any, message: str) -> None:
        self.wire.send({"id": request_id, "error": {"message": message}})

    # ------------------------------------------------------------------
    def _hello(self, params: dict) -> Any:
        """Import and verify the one package. Returns the result dict,
        or an error string — the loader's checks, relocated."""
        if params.get("protocol_version") != PROTOCOL_VERSION:
            return (f"protocol version mismatch: worker speaks "
                    f"{PROTOCOL_VERSION}")
        if self.agent is not None:
            return "already handshaken"

        folder = Path(str(params.get("folder") or ""))
        if not folder.is_dir():
            return f"agent folder '{folder}' does not exist"

        try:
            manifest = Manifest(params.get("manifest") or {})
            module_path, class_name = manifest.entrypoint
            agent_id = manifest.agent_id
        except Exception as exc:
            return f"the manifest cannot be read: {type(exc).__name__}: {exc}"
        if not isinstance(agent_id, str) or not agent_id:
            return "the manifest names no agent"
        try:
            module = self._mount(folder, manifest.agent_id, module_path)
        except Exception as exc:
            return f"entrypoint import failed: {exc}"

        entry_class = getattr(module, class_name, None)
        if entry_class is None:
            return (f"entrypoint class '{class_name}' not found in "
                    f"module '{module_path}'")
        if not (isinstance(entry_class, type) and issubclass(entry_class, AgentBase)):
            return f"entrypoint class '{class_name}' is not an AgentBase subclass"

        try:
            agent = entry_class(manifest)
        except Exception as exc:
            return f"entrypoint construction failed: {exc}"

        try:
            missing = agent.missing_functions()
            if missing:
                return f"functions declared but not implemented: {missing}"
            functions = [name for name, _, _ in manifest.functions()]
        except Exception as exc:
            return f"the agent's functions cannot be read: {exc}"

        self.agent = agent
        return {
            "agent_id": manifest.agent_id,
            "version": manifest.version,
            "functions": functions,
        }

    @staticmethod
    def _mount(folder: Path, agent_id: str, module_path: str):
        """Mount the folder as ``decentai_agents.<agent_id>`` and import
        the entry module — one agent per process, so no digest tag."""
        if PACKAGE_PREFIX not in sys.modules:
            root_spec = importlib.machinery.ModuleSpec(
                PACKAGE_PREFIX, None, is_package=True
            )
            root_spec.submodule_search_locations = []
            sys.modules[PACKAGE_PREFIX] = importlib.util.module_from_spec(root_spec)

        package_name = f"{PACKAGE_PREFIX}.{agent_id}"
        package_spec = importlib.machinery.ModuleSpec(
            package_name, None, is_package=True
        )
        package_spec.submodule_search_locations = [str(folder)]
        sys.modules[package_name] = importlib.util.module_from_spec(package_spec)

        return importlib.import_module(f"{package_name}.{module_path}")

    # ------------------------------------------------------------------
    def _invoke(self, request_id: Any, params: dict) -> None:
        if self.agent is None:
            return self._refuse(request_id, "no handshake")
        call_id = str(params.get("call_id") or "")
        name = str(params.get("function") or "")
        if not call_id:
            return self._refuse(request_id, "call_id is required")
        if call_id in self.invocations:
            return self._refuse(request_id, f"call_id '{call_id}' is already running")

        method = self.agent.function(name)
        if method is None:
            return self._refuse(request_id, f"unknown function '{name}'")

        task = asyncio.get_running_loop().create_task(
            self._run_function(request_id, call_id, name, method, params)
        )
        self.invocations[call_id] = task
        task.add_done_callback(lambda _: (self.invocations.pop(call_id, None),
                                          self.screens.pop(call_id, None)))

    async def _run_function(self, request_id: Any, call_id: str,
                            name: str, method, params: dict) -> None:
        """One invocation, start to answer. Mirrors the executor's
        post-gate semantics exactly: exceptions become error RESULTS with
        the reason attached, and anything that is not ``(dict, status)``
        is an invalid-result error. A protocol-level error would say the
        worker is broken; none of these are that."""
        # The model is asked for like any resource: call.llm() becomes an
        # llm.complete request, and whether THIS invocation may have a
        # completion is the host's per-call decision — a flag here would
        # be a claim, and the host doesn't take the worker's word.
        call = FunctionCall(
            dict(params.get("inputs") or {}),
            progress_sink=self._progress_sink(call_id),
            resources=WireResources(self._rpc, call_id),
            llm=self._llm(call_id),
            show=self._show(call_id),
            post=self._post_to(call_id),
            ask=self._ask_to(call_id),
            propose=self._propose_to(call_id),
            install=self._install_to(call_id),
            credential=self._credential_to(call_id),
            screen=self._screen_for(call_id),
            conversation=str(params.get("conversation") or ""),
        )
        try:
            # Awaited where it can be: a function written without
            # `async` has already run by now, and its answer is its
            # answer — not a failure for having no await in it.
            outcome = method(call)
            if inspect.isawaitable(outcome):
                outcome = await outcome
        except asyncio.CancelledError:
            self.wire.send({"id": request_id, "result": {
                "result": {"error": f"'{name}' was cancelled by the platform"},
                "status": "error",
            }})
            return
        except Exception as exc:
            logger.error(f"{name} raised: {exc}", exc_info=True)
            reason = str(exc).strip()[:300]
            message = f"'{name}' failed"
            self.wire.send({"id": request_id, "result": {
                "result": {"error": f"{message}: {reason}" if reason else f"{message}."},
                "status": "error",
            }})
            return

        if not (
            isinstance(outcome, tuple)
            and len(outcome) == 2
            and isinstance(outcome[0], dict)
            and outcome[1] in ("success", "error")
        ):
            self.wire.send({"id": request_id, "result": {
                "result": {"error": (
                    f"'{name}' returned an invalid result "
                    f"(expected (dict, 'success'|'error'))"
                )},
                "status": "error",
            }})
            return

        result, status = outcome
        try:
            self.wire.send({"id": request_id,
                            "result": {"result": result, "status": status}})
        except (TypeError, ValueError) as exc:
            # A result holding what JSON cannot say — a date, a set,
            # bytes. The call is answered all the same: unanswered, the
            # platform waits out the whole timeout for a function that
            # finished long ago.
            self.wire.send({"id": request_id, "result": {
                "result": {"error": (
                    f"'{name}' returned a result that is not JSON "
                    f"({str(exc)[:200]}) — return text, numbers, lists "
                    f"and objects")},
                "status": "error",
            }})

    def _cancel(self, request_id: Any, params: dict) -> None:
        task = self.invocations.get(str(params.get("call_id") or ""))
        if task is not None:
            task.cancel()
        self.wire.send({"id": request_id,
                        "result": {"cancelled": task is not None}})

    # ------------------------------------------------------------------
    # Requests TO the host
    # ------------------------------------------------------------------
    async def _rpc(self, method: str, params: dict) -> Any:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self.wire.send({"id": request_id, "method": method, "params": params})
        try:
            return await future
        finally:
            self._pending.pop(request_id, None)

    def _settle(self, message: dict) -> None:
        future = self._pending.get(message.get("id"))
        if future is None or future.done():
            return
        if "error" in message:
            detail = (message.get("error") or {}).get("message") or "refused"
            future.set_exception(ResourceDenied(detail))
        else:
            future.set_result(message.get("result"))

    def _screen_for(self, call_id: str):
        """This call's screen: frames go out as notifications, input
        comes back to it by call_id, and it is forgotten with the
        call — the host hears screen.closed if it was ever shown."""
        async def send(method: str, params: dict) -> None:
            self.wire.send({"method": method,
                            "params": {"call_id": call_id, **params}})
        screen = Screen(send)
        self.screens[call_id] = screen
        return screen

    def _progress_sink(self, call_id: str):
        async def sink(description: str) -> None:
            self.wire.send({"method": "progress",
                            "params": {"call_id": call_id,
                                       "description": description}})
        return sink

    def _llm(self, call_id: str):
        async def complete(messages, max_tokens=None, images=None) -> str:
            params = {"call_id": call_id,
                      "messages": messages, "max_tokens": max_tokens}
            if images:
                # Named, never carried: the host reads each file under
                # this call's grant and shows the model the bytes.
                params["images"] = list(images)
            answer = await self._rpc("llm.complete", params)
            answer = answer or {}
            return Completion(answer.get("text"), answer.get("stop_reason"))
        return complete

    def _show(self, call_id: str):
        # An offer is a request like any other: the host checks it, keeps
        # it, and answers with its id — or with none, where nobody could
        # see it. A refusal arrives as ResourceDenied, saying why.
        async def offer(spec: dict):
            answer = await self._rpc("show", {"call_id": call_id, **spec})
            return (answer or {}).get("display_id")
        return offer

    def _post_to(self, call_id: str):
        # Speaking is asked for like anything else: the host decides
        # whether there is a chat to speak in, and checks what is said.
        async def post(text: str, displays: list) -> bool:
            answer = await self._rpc("post", {
                "call_id": call_id, "text": text, "displays": displays})
            return bool((answer or {}).get("posted"))
        return post

    def _credential_to(self, call_id: str):
        # A login is asked for like a question: the host holds the
        # function's clock while the person types, allows or chooses.
        async def credential(host: str, fields: list, account, site, refresh):
            params = {"call_id": call_id, "host": host, "fields": fields,
                      "refresh": bool(refresh)}
            if account:
                params["account"] = account
            if site:
                params["site"] = site
            answer = await self._rpc("credential", params)
            return (answer or {}).get("values")
        return credential

    def _ask_to(self, call_id: str):
        # A question is a request like any other; its answer may take a
        # person hours, and the host holds the function's clock meanwhile.
        async def ask(question: str, choices: list, expects: str = ""):
            params = {"call_id": call_id, "question": question,
                      "choices": choices}
            if expects:
                params["expects"] = expects
            answer = await self._rpc("ask", params)
            return (answer or {}).get("answer")
        return ask

    def _install_to(self, call_id: str):
        # Packages are asked for like any resource: whether THIS call
        # may have them is the host's decision, per call.
        async def install(packages: list):
            answer = await self._rpc("install", {"call_id": call_id,
                                                 "packages": packages})
            return (answer or {}).get("folder")
        return install

    def _propose_to(self, call_id: str):
        # Code put before the person is a question too: the host checks
        # it, has it read, and holds the function's clock meanwhile.
        async def propose(code: dict):
            answer = await self._rpc("propose", {"call_id": call_id,
                                                 "code": code})
            return (answer or {}).get("allowed")
        return propose

    # ------------------------------------------------------------------
    async def _close_agent(self) -> None:
        for task in list(self.invocations.values()):
            task.cancel()
        if self.agent is not None:
            try:
                await self.agent.close()
            except Exception as exc:  # closing is best-effort, always
                logger.warning(f"agent close failed: {exc}")


async def _run() -> Wire:
    wire = Wire()
    await AgentWorker(wire).run()
    return wire


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    wire = asyncio.run(_run())
    wire.flush()
    # A pipe thread may still be blocked in a read the OS will never
    # finish; a normal interpreter teardown races it (an access
    # violation on Windows). Everything worth doing is done — the agent
    # closed, the last answer flushed — so leave without ceremony.
    os._exit(0)


if __name__ == "__main__":
    main()
