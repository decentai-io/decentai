"""Running workers, and the answers to what they ask.

One pool per process: approved agent -> one live WorkerHandle, started
on first invocation in that agent's own place (confinement.py), kept
warm, replaced after death. The pool is also the ROUTER — the single
place a worker's asks (resources.*, llm.complete, show, post, ask,
propose, install, credential) are answered, by resolving the ask's
call_id to the Call that is entitled to answer it:

    call = Call(...)                     built per invocation by the executor
    pool.invoke(agent, function, inputs, call, timeout)

The Call (execution/call.py) carries authority the worker never holds:
the mediated resources built from the function's declared operations,
the chat's model when — and only when — the function declared ``llm:
true``, the chat's doors. An ask with an unknown call_id, an undeclared
operation, or no model behind it is refused there, and the refusal
surfaces in the agent's function as ResourceDenied.

Two rules keep one invocation's authority from another's:

- **A worker per approved agent, not per package.** Two organizations
  approving the same bytes get two processes. A shared process would
  see both organizations' call ids in plain view, and agent code both
  of them trusted could use one's id during the other's invocation.
  The key is the agent id everything outside the package speaks — the
  approval's ref (agents/approved.py) — so the isolation follows the
  trust decision, at the cost of one process per organization per
  agent.
- **An ask is answered only from the worker its invocation was sent
  to.** The call is bound to the handle before the invoke goes out,
  and the router checks the asking handle against it. A call id alone,
  however it was learned, opens nothing.

Timeouts are the host's clock: past the function's ``timeout_seconds``
the pool cancels cooperatively, waits the protocol's grace, then kills
the process — failing that worker's other in-flight invocations
honestly rather than guessing.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any, Dict, Optional, Tuple

from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.library import InstalledAgent
from ai_runtime.agents.workers import WorkerError, WorkerHandle


class WorkerPool:
    def __init__(self, confinement: Confinement) -> None:
        #: Where each agent's worker runs, and what confines it there.
        self.confinement = confinement
        self._handles: Dict[str, WorkerHandle] = {}
        #: agent ref -> the digest its worker is running. Handles are
        #: keyed by approval and a handle never sees a digest, so this
        #: is how the host answers "is this code in use?" before it
        #: deletes the folder a live process is running from.
        self._running: Dict[str, str] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        #: call_id -> the live Call (execution/call.py) that answers it.
        self._calls: Dict[str, Any] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # One invocation, end to end
    # ------------------------------------------------------------------

    async def invoke(self, agent: InstalledAgent, function: str, inputs: dict,
                     call: Any, timeout: float,
                     conversation: str = "") -> Tuple[dict, str]:
        """Run one function in the agent's worker, as ``call``. Returns
        the function's ``(result, status)``. Raises TimeoutError past the
        host's clock and WorkerError when the worker itself is broken —
        the caller words both for the proposing loop."""
        self._calls[call.call_id] = call
        handle = None
        before: Dict[str, int] = {}
        try:
            handle = await self._handle(agent)
            call.handle = handle
            handle.place.block(call.blocked)
            before = handle.place.reached()
            try:
                return await self._within(
                    handle.invoke(call.call_id, function, inputs, conversation),
                    call, timeout,
                )
            except asyncio.TimeoutError:
                await self._overrule(handle, call.call_id)
                raise
            except asyncio.CancelledError:
                # The platform cancelled the job. Dropping our side of
                # the wait is not cancelling the work: the worker's task
                # runs on unless told, and the job would read cancelled
                # while the write it was making lands. Shielded, so a
                # second cancel cannot cut the order short.
                await asyncio.shield(self._overrule(handle, call.call_id))
                raise
        finally:
            self._calls.pop(call.call_id, None)
            if handle is not None:
                # What was opened for this call closes with it: the
                # worker goes on to serve somebody else's call. And what
                # the worker reached meanwhile is the call's, for the
                # trail — a worker serving two calls at once counts for
                # both.
                call.close()
                call.reached = {
                    host: count - before.get(host, 0)
                    for host, count in handle.place.reached().items()
                    if count > before.get(host, 0)}

    @staticmethod
    async def _within(work, call: Any, timeout: float):
        """The function's own clock: ``timeout`` seconds of its work,
        stopped while it waits on a person (call.ask) — a question can
        take hours, and that wait is bounded where it is kept (the
        session's day). Raises TimeoutError as wait_for would."""
        loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(work)
        started = loop.time()
        try:
            while True:
                if call.waiting:
                    budget = 1.0        # paused: look again in a moment
                else:
                    budget = timeout - (loop.time() - started - call.waited)
                    if budget <= 0:
                        raise asyncio.TimeoutError()
                done, _ = await asyncio.wait({task}, timeout=budget)
                if done:
                    return task.result()
        finally:
            if not task.done():
                task.cancel()

    async def _overrule(self, handle: WorkerHandle, call_id: str) -> None:
        """Cancel cooperatively; a worker that cannot even answer the
        cancel within the grace is killed — which a process of its own
        makes possible, and code run inside the host would not."""
        try:
            await asyncio.wait_for(handle.cancel(call_id),
                                   WorkerHandle.GRACE_SECONDS)
        except (WorkerError, asyncio.TimeoutError):
            self.logger.error("worker ignored a cancel; killing it")
            await handle.kill()

    # ------------------------------------------------------------------
    # The workers
    # ------------------------------------------------------------------

    async def _handle(self, agent: InstalledAgent) -> WorkerHandle:
        """The approved agent's live worker, started if it is not
        running. Keyed by the agent id, not the digest: one process per
        approval of a package, never one shared between organizations.
        A handshake failure raises WorkerError — installation verified
        this package once, so failing HERE means the disk or the env
        changed underneath us, which deserves a loud answer."""
        key = agent.agent_id
        handle = self._handles.get(key)
        if self._current(handle, agent):
            return handle

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            handle = self._handles.get(key)
            if self._current(handle, agent):
                return handle
            # An update keeps the approval and changes the code: the
            # worker still up is running the version before, and would
            # answer "unknown function" for anything the new one added.
            if handle is not None:
                if handle.alive:
                    self.logger.info(f"Worker retired: {key} runs "
                                     f"{self._running.get(key, '')[:19]}…, "
                                     f"approved is {agent.digest[:19]}…")
                # A dead one too: its death clears the place the next
                # worker is about to be started in, and must be over.
                await self.discard(key)
            # The worker speaks the platform's SDK, not the one copied
            # in the day the environment was built.
            for problem in await agent.environment.refresh_sdk():
                self.logger.warning(
                    f"SDK not refreshed for {agent.agent_id}: {problem}")
            handle = WorkerHandle(
                agent.environment.python, agent.folder, agent.manifest.document,
                # Its own user and place, per approval, as the worker
                # itself is.
                self.confinement.place(key),
            )
            # Each callback knows which worker is speaking: the router
            # answers an ask only for an invocation sent to that worker.
            handle.router = partial(self._route, handle)
            handle.progress = partial(self._progress, handle)
            handle.screen = partial(self._screen, handle)
            errors = await handle.start()
            if errors:
                raise WorkerError("; ".join(errors))
            self._handles[key] = handle
            self._running[key] = agent.digest
            self.logger.info(f"Worker up: {key} ({agent.digest[:19]}…)")
            return handle

    def _current(self, handle: Optional[WorkerHandle],
                 agent: InstalledAgent) -> bool:
        """Alive, and running the code this approval now names."""
        return (handle is not None and handle.alive
                and self._running.get(agent.agent_id) == agent.digest)

    def live_digests(self) -> set:
        """The code live workers are running right now — what must not be
        deleted underneath them."""
        return {digest for digest in self._running.values() if digest}

    async def discard(self, agent_id: str) -> None:
        """Stop the agent's worker, if one runs."""
        handle = self._handles.pop(agent_id, None)
        self._running.pop(agent_id, None)
        if handle is not None:
            await handle.stop()

    async def stop(self) -> None:
        """End every worker. The process shutdown hook."""
        for agent_id in list(self._handles):
            await self.discard(agent_id)

    async def terminate(self) -> None:
        """End every worker where it runs, without asking — for a
        shutdown that could not ask politely in time. The container
        ends every process of each agent's, and takes away what they
        left."""
        for handle in self._handles.values():
            if handle.alive:
                await self.confinement.container.stop(handle.place.name)
        self._handles.clear()
        self._running.clear()
        self._calls.clear()

    # ------------------------------------------------------------------
    # The router — every ask a worker can make, answered by its call
    # ------------------------------------------------------------------

    def _call_of(self, handle: WorkerHandle, call_id: str) -> Any:
        """The live call this worker may speak for under this id, or
        None: a call id alone, however it was learned, opens nothing."""
        call = self._calls.get(str(call_id or ""))
        return call if call is not None and call.handle is handle else None

    async def _route(self, handle: WorkerHandle, method: str,
                     params: dict) -> Any:
        call = self._call_of(handle, params.get("call_id"))
        if call is None:
            raise WorkerError("no invocation of this worker is entitled "
                              "to this ask")
        return await call.answer(method, params)

    async def _progress(self, handle: WorkerHandle, call_id: str,
                        description: str) -> None:
        call = self._call_of(handle, call_id)
        if call is not None:
            await call.progress(description)

    async def _screen(self, handle: WorkerHandle, call_id: str, kind: str,
                      params: Dict[str, Any]) -> None:
        """A frame, or the end, of the screen one call shows — from the
        worker it was sent to and no other."""
        call = self._call_of(handle, call_id)
        if call is not None:
            await call.screen(kind, params)

    async def screen_input(self, call_id: str, events: list) -> bool:
        """The person acting on a screen: to the worker running the
        call, as a notification. False when no such call runs."""
        call = self._calls.get(call_id)
        if call is None or call.handle is None:
            return False
        await call.handle.notify("screen.input", {
            "call_id": call_id, "events": list(events)})
        return True
