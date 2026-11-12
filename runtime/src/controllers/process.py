"""One agent's program, running as one process: the agents standard spoken with it through the sandboxes (agents.md
§4, §9; runtime.md §4). It knows the manifest and the messages, and nothing of who uses it."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import itertools
import json
import logging
import posixpath
import secrets
import time
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator

log = logging.getLogger("runtime")


class Process:
    """One process of an agent's release, given:

    - ``sandboxes``, what it is started, fed and ended through (Sandboxes), and ``name``, its name there;
    - ``agent``, as a contract gives it: ``agent_id``, ``version``, ``manifest``, ``base``, ``programs``;
    - ``serve(call, function, inputs, files)``: a call the program makes within ``call``, one of ours in flight, and of
      something that call's function declares in ``calls``. It answers ``(output, files)``, the files offered as
      ``{id, name, size}``, or raises Process.Failed;
    - ``noted(call, note)``: the program's note on a call, ``{"progress": …}`` or ``{"screen": …}``;
    - ``keep(name, data)``: a file the program returned, kept; it answers the file's ref.

    Its owner hands it what the sandboxes say of it: ``received(message)`` and ``ended(reason)``. A call of ours in
    flight is ``{id, function, done, deciding}`` and the context its owner gave it: while ``deciding`` is above 0 a
    person decides, and its clock stops."""

    class Failed(Exception):
        """A call that ended without a result: its code (refused, not_found, invalid, cancelled, failed, or a
        program's own) and its words."""

        def __init__(self, code: str, message: str):
            super().__init__(message)
            self.code, self.message = code, message[:1000]

    def __init__(self, sandboxes: Any, name: str, agent: dict[str, Any],
                 serve: Callable[[Any, str, dict[str, Any], list[dict[str, Any]]], Awaitable[tuple[Any, list]]],
                 noted: Callable[[Any, dict[str, Any]], Awaitable[None]],
                 keep: Callable[[str, bytes], Awaitable[str]], *,
                 file_most: int, ready_seconds: int = 60, cancel_seconds: int = 5):
        self.sandboxes, self.name, self.agent, self.manifest = sandboxes, name, agent, agent["manifest"]
        self.serve, self.noted, self.keep = serve, noted, keep
        self.file_most, self.ready_seconds, self.cancel_seconds = file_most, ready_seconds, cancel_seconds
        #: Our calls in flight, by id; the program's calls being served, by their _id, and the call each is within.
        self.calls: dict[str, SimpleNamespace] = {}
        self.serving: dict[str, asyncio.Task] = {}
        self.within: dict[str, str] = {}
        self.ready: asyncio.Future = asyncio.get_running_loop().create_future()
        #: Why it ended, once it has; why its owner, or it, asked it to end (idle, replaced, stopped or failed), once
        #: one did; when it was last used.
        self.gone, self.ending, self.used_at = "", "", time.monotonic()
        #: Ids from a random start, so no two processes or placements of a chat give a message the same id.
        self._ids = itertools.count(secrets.randbelow(2 ** 40))
        self._tasks: set[asyncio.Task] = set()

    @staticmethod
    def release(agent: dict[str, Any]) -> str:
        """What a process runs, as one digest: the agent's manifest, base and programs. Never its id, which is only the
        name a developer chose."""
        content = {"manifest": agent["manifest"], "base": agent.get("base", ""),
                   "programs": {machine: program["digest"] for machine, program in agent["programs"].items()}}
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    @property
    def busy(self) -> bool:
        return bool(self.calls or self.serving)

    def function(self, function_id: str) -> dict[str, Any] | None:
        return next((f for f in self.manifest["functions"] if f["id"] == function_id), None)

    # -- what its owner asks of it --------------------------------------------------------------------

    async def start(self, network: bool = True) -> None:
        """Started, and ready within ready_seconds: a ready naming the manifest's agent, version and functions. It has
        a network only if asked and its manifest declares a host or a function calls network.open. Process.Failed when
        it doesn't start, and it is ended."""
        hosts = (self.manifest.get("network") or {}).get("hosts") or []
        opens = any("network.open" in (f.get("calls") or []) for f in self.manifest["functions"])
        try:
            await self.sandboxes.start(self.name, {m: p["digest"] for m, p in self.agent["programs"].items()},
                                       self.agent.get("base", ""), network and bool(hosts or opens))
            await asyncio.wait_for(asyncio.shield(self.ready), self.ready_seconds)
        except (ConnectionError, TimeoutError, self.Failed) as failure:
            await self.end("failed")
            why = str(failure) or f"it said no ready within {self.ready_seconds} seconds"
            raise self.Failed("failed", f"{self.agent['agent_id']} did not start: {why}") from None

    async def call(self, function_id: str, inputs: dict[str, Any], files: list[dict[str, Any]] = (),
                   **context: Any) -> tuple[Any, list[str]]:
        """A function's output and the refs of the files it returned, its inputs and output held to the manifest.
        ``files``, each ``{id, name, size}``, go in written in the inputs by their id; files coming out are kept, and
        their refs written in the output in place of their paths. ``context`` stays on the call, handed back with it to
        ``serve`` and ``noted``. Its time is the function's timeout_seconds. A caller that gives up cancels it; once it
        ends, the program's own calls within it stop."""
        function = self.function(function_id)
        if function is None:
            raise self.Failed("not_found", f"{self.agent['agent_id']} has no function {function_id}.")
        problem = next(Draft202012Validator(function.get("inputs") or {}).iter_errors(inputs), None)
        if problem is not None:
            raise self.Failed("invalid", f"The inputs don't fit {function_id}: {problem.message[:300]}")
        call = SimpleNamespace(**context, id=f"r{next(self._ids)}", function=function_id, deciding=0,
                               done=asyncio.get_running_loop().create_future())
        self.calls[call.id] = call
        sent = handed = False
        try:
            inputs = self.swapped(inputs, {f["id"]: f"in/{f['id']}/{f['name']}" for f in files})
            await self._send(call.id, "call", {"function": function_id, "inputs": inputs}, files)
            sent = True
            message = await self._waited(call, int(function.get("timeout_seconds") or 60))
        except asyncio.CancelledError:
            if sent:
                handed = True
                self._later(self.cancel(call.id))
            raise
        except ConnectionError as failure:
            raise self.Failed("failed", str(failure)) from None
        finally:
            if not handed:
                self.calls.pop(call.id, None)
            for message_id in [m for m, within in self.within.items() if within == call.id and m in self.serving]:
                self.serving[message_id].cancel()
            self.used_at = time.monotonic()
        body = message.get("body") or {}
        data = body.get("data") or {}
        if body.get("type") == "error":
            raise self.Failed(str(data.get("code") or "failed"), str(data.get("message") or "The agent failed."))
        output, made = await self._kept(data.get("output") or {}, message.get("files"))
        problem = next(Draft202012Validator(function.get("outputs") or {}).iter_errors(output), None)
        if problem is not None:
            raise self.Failed("invalid", f"{self.agent['agent_id']} answered outside its outputs: "
                                         f"{problem.message[:300]}")
        return output, made

    async def cancel(self, call_id: str) -> None:
        """A call of ours cancelled. It has cancel_seconds to end, or the process is ended, with every call on it."""
        call = self.calls.get(call_id)
        if call is None:
            return
        with contextlib.suppress(ConnectionError):
            await self._send(f"x{next(self._ids)}", "cancel", {"call": call_id})
        done, _ = await asyncio.wait({call.done}, timeout=self.cancel_seconds)
        if not done:
            await self.end("failed")
            self._settle(call, "cancelled", "Cancelled.")
        self.calls.pop(call_id, None)

    async def screen(self, call_id: str, events: list[dict[str, Any]]) -> None:
        """What a person did on the screen a call of ours shows, handed to the program."""
        if call_id in self.calls:
            await self._send(f"s{next(self._ids)}", "note", {"call": call_id, "input": events})

    async def end(self, why: str = "stopped") -> None:
        """Asked to end, the first ``why`` kept; ``ended`` follows."""
        if not self.gone:
            self.ending = self.ending or why
            await self.sandboxes.end(self.name)

    # -- what the sandboxes say of it -----------------------------------------------------------------

    async def received(self, message: dict[str, Any]) -> None:
        """What the program said: ready; the result or error of one of our calls; an error of its own; a call of its
        own, or its cancel; a note on one of our calls; a log line."""
        self.used_at = time.monotonic()
        body = message.get("body") if isinstance(message.get("body"), dict) else {}
        kind, data = body.get("type"), body.get("data") if isinstance(body.get("data"), dict) else {}
        call = self.calls.get(str(data.get("call") or ""))
        if kind == "log":
            log.debug(f"{self.name}: {str(data.get('line'))[:300]}")
        elif kind == "ready" and not self.ready.done():
            expected = {"agent": self.agent["agent_id"], "version": self.agent["version"],
                        "functions": sorted(f["id"] for f in self.manifest["functions"])}
            if {**data, "functions": sorted(data.get("functions") or [])} == expected:
                self.ready.set_result(True)
            else:
                self.ready.set_exception(self.Failed("failed", "Its ready doesn't match its manifest."))
        elif kind in ("result", "error") and call is not None:
            if not call.done.done():
                call.done.set_result(message)
        elif kind == "error" and "call" not in data:  # the program itself failed: its calls fail with its words
            why = f"The agent failed: {str(data.get('message') or 'it gave no reason')[:500]}"
            for each in self.calls.values():
                self._settle(each, "failed", why)
            await self.end("failed")
        elif kind == "call" and isinstance(message.get("_id"), str):
            self.within[message["_id"]] = str(data.get("within") or "")
            self.serving[message["_id"]] = self._later(self._served(message))
        elif kind == "cancel" and data.get("call") in self.serving:
            self.serving[data["call"]].cancel()
        elif kind == "note" and call is not None:
            note = {key: data[key] for key in ("progress", "screen") if key in data}
            if note:
                await self.noted(call, note)

    async def ended(self, reason: str) -> None:
        """Every call of ours in flight fails with the reason; the program's calls being served stop."""
        self.gone = reason or "ended"
        for call in self.calls.values():
            self._settle(call, "failed", f"The agent's process ended: {self.gone}.")
        for task in self.serving.values():
            task.cancel()
        if not self.ready.done():
            self.ready.set_exception(self.Failed("failed", f"it ended before it was ready: {self.gone}"))

    # -- within ---------------------------------------------------------------------------------------

    async def _served(self, message: dict[str, Any]) -> None:
        """One of the program's calls, within one of ours in flight and of something its function declares: answered
        with its result or its error."""
        data, message_id = message["body"].get("data") or {}, message["_id"]
        try:
            call = self.calls.get(str(data.get("within") or ""))
            if call is None:
                raise self.Failed("refused", "A call is made within a call of the runtime's that is in flight.")
            name = str(data.get("function") or "")
            inputs = data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
            asked = f"{name}:{inputs.get('resource')}" if name.startswith("record.") else name
            if asked not in ((self.function(call.function) or {}).get("calls") or []):
                raise self.Failed("refused", f"{self.agent['agent_id']}.{call.function} doesn't declare {asked}.")
            output, files = await self.serve(call, name, inputs, message.get("files") or [])
            answer = ("result", {"call": message_id, "output": output}, files)
        except self.Failed as failed:
            answer = ("error", {"call": message_id, "code": failed.code, "message": failed.message}, [])
        except asyncio.CancelledError:
            answer = ("error", {"call": message_id, "code": "cancelled", "message": "Cancelled."}, [])
        except Exception as error:
            log.warning(f"{self.name}: a call to the runtime failed: {error!r}")
            answer = ("error", {"call": message_id, "code": "failed", "message": str(error)[:500] or "It failed."}, [])
        finally:
            self.serving.pop(message_id, None)
            self.within.pop(message_id, None)
        with contextlib.suppress(ConnectionError):
            await self._send(f"a{next(self._ids)}", *answer)

    async def _waited(self, call: SimpleNamespace, seconds: int) -> dict[str, Any]:
        """The call's answer within its time, which stops while a person decides; past it, cancelled and failed."""
        left = float(seconds)
        while left > 0:
            done, _ = await asyncio.wait({call.done}, timeout=1)
            if done:
                return call.done.result()
            left -= 0 if call.deciding else 1
        await self.cancel(call.id)
        raise self.Failed("failed", f"The call took longer than its {seconds} seconds.")

    async def _kept(self, value: Any, files: Any) -> tuple[Any, list[str]]:
        """The files the program returned, pulled and kept, their refs in place of their paths; and the refs."""
        found = {}
        for file in files if isinstance(files, list) else []:
            try:
                data = await self.sandboxes.pull(file["id"], self.file_most)
            except ValueError:
                raise self.Failed("invalid", f"{file['path']} is larger than a file may be: "
                                             f"{self.file_most} bytes.") from None
            except ConnectionError as failure:
                raise self.Failed("failed", f"{file['path']} could not be had: {failure}") from None
            found[file["path"]] = await self.keep(posixpath.basename(file["path"]), data)
        return self.swapped(value, found), list(found.values())

    def _settle(self, call: SimpleNamespace, code: str, message: str) -> None:
        if not call.done.done():
            call.done.set_result({"body": {"type": "error", "data": {"code": code, "message": message}}})

    async def _send(self, message_id: str, kind: str, data: dict[str, Any], files: list[dict] = ()) -> None:
        await self.sandboxes.send(self.name, {"_id": message_id, "body": {"type": kind, "data": data}}, list(files))

    def _later(self, work: Awaitable[Any]) -> asyncio.Task:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @classmethod
    def swapped(cls, value: Any, found: dict[str, str]) -> Any:
        """Every text equal to a key of ``found`` replaced by its value, however deep."""
        if isinstance(value, str):
            return found.get(value, value)
        if isinstance(value, list):
            return [cls.swapped(item, found) for item in value]
        if isinstance(value, dict):
            return {key: cls.swapped(item, found) for key, item in value.items()}
        return value
