"""Running workers, and the answers to what they ask.

One pool per process is the intent: approved agent -> one live
WorkerHandle, spawned on first invocation, kept warm, replaced after
death. The pool is also the ROUTER — the single place a worker's asks
(resources.*, llm.complete, show, post, ask, propose, install,
credential) are answered, by resolving the ask's call_id to the
invocation that is entitled to answer it:

    context = CallContext(resources, llm, progress)   built per invocation
    pool.invoke(agent, call_id, function, inputs, context, timeout)

The context carries authority the worker never holds: the mediated
ResourceAccess built from the function's declared operations, the
chat's model when — and only when — the function declared ``llm: true``,
and the chat's progress sink. An ask with an unknown call_id, an
undeclared operation, or no model behind it is refused here, and the
refusal surfaces in the agent's function as ResourceDenied.

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
  to.** The context is bound to the handle before the invoke goes out,
  and the router checks the asking handle against it. A call id alone,
  however it was learned, opens nothing.

Timeouts are the host's clock: past the function's ``timeout_seconds``
the pool cancels cooperatively, waits the protocol's grace, then kills
the process — failing that worker's other in-flight invocations
honestly rather than guessing.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
from functools import partial
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

from ai_runtime.agents.confinement import Confinement
from ai_runtime.agents.library import InstalledAgent
from ai_runtime.agents.worker_handle import WorkerError, WorkerHandle
from ai_runtime.runtime_logging import RuntimeLoggerFactory

#: The one refusal call.llm() can meet — same wording as the SDK's own,
#: so the function's author reads one message wherever it runs.
LLM_UNAVAILABLE = (
    "The platform model is not available here — the function must "
    "declare `llm: true` in its manifest, and the invocation must run "
    "inside a chat with a model."
)

#: method suffix -> the parameters it takes, in call order.
RESOURCE_METHODS = {
    "use_secret": ("resource_id", "ref"),
    "list_secrets": ("resource_id",),
    "list_data": ("resource_id", "filters"),
    "read_data": ("resource_id", "ref"),
    "create_data": ("resource_id", "fields"),
    "update_data": ("resource_id", "ref", "fields"),
    "delete_data": ("resource_id", "ref"),
    "list_files": ("resource_id",),
    "read_file": ("resource_id", "ref"),
    "create_file": ("resource_id", "filename", "content", "content_base64"),
    "delete_file": ("resource_id", "ref"),
}


class CallContext:
    """What one invocation is entitled to — built by the executor from
    the function's manifest declarations, resolved per ask."""

    def __init__(self, resources: Any, llm: Optional[Callable] = None,
                 progress: Optional[Callable] = None,
                 show: Optional[Callable] = None,
                 post: Optional[Callable] = None,
                 ask: Optional[Callable] = None,
                 credential: Optional[Callable] = None,
                 screen: Optional[Callable] = None,
                 propose: Optional[Callable] = None,
                 install: Optional[Callable] = None,
                 blocked: Optional[List[str]] = None):
        self.resources = resources
        #: Sites no agent may open in this deployment, told to the
        #: worker's proxy pass as the call begins.
        self.blocked = list(blocked or [])
        #: host -> connections this call's worker made while it ran:
        #: filled in when the call ends, for the audit trail. A worker
        #: serving two calls at once counts for both.
        self.reached: Dict[str, int] = {}
        #: Hosts a credential handed to this call named (``from_secret``),
        #: open on the worker's way out until the call ends.
        self.learned: list = []
        #: async (kind, params) -> None: a screen the function shows —
        #: "frame" with the picture, "closed" when it stops. None where
        #: nobody could watch (a test).
        self.screen = screen
        self.llm = llm
        self.progress = progress
        #: async (host, fields, account, site, refresh) -> values | None:
        #: a login the function asks for as it works. None where the
        #: manifest did not declare credentials, or nobody could answer.
        self.credential = credential
        #: async (display spec) -> display id: what the call offers to
        #: show, checked and kept. None where nobody could see it.
        self.show = show
        #: async (text, display ids) -> bool: the agent speaking for
        #: itself. None where there is no chat to speak in.
        self.post = post
        #: async (question, choices) -> answer | None: the agent asking
        #: the person. None where there is no one to ask.
        self.ask = ask
        #: async (code) -> allowed | None: code the function wants to
        #: run, put before the person. None where there is no one to ask.
        self.propose = propose
        #: async (packages) -> folder: packages a card the person
        #: allowed named, installed for this call. None unless the
        #: function declared ``code: true``.
        self.install = install
        #: The function's clock stops while a person decides: how many
        #: questions are open, and how long answered ones waited.
        self.waiting = 0
        self.waited = 0.0
        #: The worker the invocation was sent to, bound by the pool
        #: before the invoke goes out. An ask is answered only from it.
        self.handle: Optional[WorkerHandle] = None
        #: The invocation's own context (CURRENT_CHAT, CURRENT_JOB_ID),
        #: taken by the pool when the invoke goes out. A worker outlives
        #: the chat that spawned it, and its reader task carries THAT
        #: chat's context — so an ask answered there would reach the
        #: platform as the first chat, on a delegation long expired.
        self.scope: Optional[contextvars.Context] = None


class WorkerPool:
    #: Every live pool, for test teardown (terminate_all). Production
    #: holds one pool for the process's lifetime and never calls it.
    _live: ClassVar[List["WorkerPool"]] = []

    def __init__(self) -> None:
        self._handles: Dict[str, WorkerHandle] = {}
        #: agent ref -> the digest its worker is running. Handles are
        #: keyed by approval and a handle never sees a digest, so this
        #: is how the host answers "is this code in use?" before it
        #: deletes the folder a live process is running from.
        self._running: Dict[str, str] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._calls: Dict[str, CallContext] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)
        WorkerPool._live.append(self)

    def _bind_loop(self) -> None:
        """A handle's tasks, futures and pipes belong to the loop that
        started it; handing one to a different loop writes into a dead
        transport. Production runs one loop forever, so this never
        fires there — but a pool that outlives its loop (test runners
        do this) starts over rather than lying."""
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self.terminate()
            self._locks.clear()
            self._loop = loop

    # ------------------------------------------------------------------
    # One invocation, end to end
    # ------------------------------------------------------------------

    async def invoke(self, agent: InstalledAgent, call_id: str,
                     function: str, inputs: dict, context: CallContext,
                     timeout: int, conversation: str = "") -> Tuple[dict, str]:
        """Run one function in the agent's worker. Returns the
        function's ``(result, status)``. Raises TimeoutError past the
        host's clock and WorkerError when the worker itself is broken —
        the caller words both for the proposing loop."""
        self._bind_loop()
        context.scope = contextvars.copy_context()
        self._calls[call_id] = context
        place, before = None, {}
        try:
            handle = await self._handle(agent)
            context.handle = handle
            place = getattr(handle, "place", None)
            if place is not None:
                place.block(context.blocked)
                before = place.reached()
            try:
                return await self._within(
                    handle.invoke(call_id, function, inputs, conversation),
                    context, timeout,
                )
            except asyncio.TimeoutError:
                await self._overrule(handle, call_id)
                raise
            except asyncio.CancelledError:
                # The platform cancelled the job. Dropping our side of
                # the wait is not cancelling the work: the worker's task
                # runs on unless told, and the job would read cancelled
                # while the write it was making lands. Shielded, so a
                # second cancel cannot cut the order short.
                await asyncio.shield(self._overrule(handle, call_id))
                raise
        finally:
            self._calls.pop(call_id, None)
            if place is not None:
                # What this call's credential opened closes with it: the
                # worker goes on to serve somebody else's call.
                place.take_back(context.learned)
                context.learned = []
                context.reached = {
                    host: count - before.get(host, 0)
                    for host, count in place.reached().items()
                    if count > before.get(host, 0)}

    @staticmethod
    async def _within(work, context: CallContext, timeout: float):
        """The function's own clock: ``timeout`` seconds of its work,
        stopped while it waits on a person (call.ask) — a question can
        take hours, and that wait is bounded where it is kept (the
        session's day). Raises TimeoutError as wait_for would."""
        loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(work)
        started = loop.time()
        try:
            while True:
                if context.waiting:
                    budget = 1.0        # paused: look again in a moment
                else:
                    budget = timeout - (loop.time() - started - context.waited)
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
        """The approved agent's live worker, spawned if it is not
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
            refresh = getattr(agent.environment, "refresh_sdk", None)
            for problem in (refresh() if refresh is not None else []):
                self.logger.warning(
                    f"SDK not refreshed for {agent.agent_id}: {problem}")
            handle = WorkerHandle(
                agent.environment.python, agent.folder, agent.manifest.document,
                # Its own user, where workers are confined — per
                # approval, as the worker itself is.
                place=Confinement.place_for(key),
            )
            handle.agent_ref = key
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

    def terminate(self) -> None:
        """Kill every worker process, synchronously, best-effort — for
        teardown paths that have no event loop left to be polite on."""
        for handle in self._handles.values():
            process = handle.process
            if process is not None and process.returncode is None:
                if handle.place is not None:
                    # Another user's process: the helper ends it, and
                    # takes away what it left.
                    handle.place.stop()
                    handle.place.clear()
                    continue
                try:
                    process.kill()
                except Exception:
                    # The transport may belong to a closed loop; the OS
                    # does not care which loop a pid came from.
                    # A worker in the agents' container has no pid here.
                    try:
                        if isinstance(process.pid, int):
                            os.kill(process.pid, 15)
                    except OSError:
                        pass
        self._handles.clear()
        self._running.clear()
        self._calls.clear()

    @classmethod
    def terminate_all(cls) -> None:
        for pool in cls._live:
            pool.terminate()
        cls._live.clear()

    # ------------------------------------------------------------------
    # The router — every ask a worker can make, answered per call_id
    # ------------------------------------------------------------------

    async def _route(self, handle: WorkerHandle, method: str,
                     params: dict) -> Any:
        context = self._calls.get(str(params.get("call_id") or ""))
        if context is None or context.handle is not handle:
            raise WorkerError("no invocation of this worker is entitled "
                              "to this ask")
        return await self._as_invocation(
            context, self._answer(context, method, params))

    @staticmethod
    async def _as_invocation(context: CallContext, work) -> Any:
        """Run an ask's answer in the context of the invocation entitled
        to it, not the one the worker was spawned in. A copy per ask, so
        one ask's settings never leak into another."""
        if context.scope is None:
            return await work
        return await asyncio.get_running_loop().create_task(
            work, context=context.scope.copy())

    async def _answer(self, context: CallContext, method: str,
                      params: dict) -> Any:
        if method == "llm.complete":
            if context.llm is None:
                raise WorkerError(LLM_UNAVAILABLE)
            images = [i for i in params.get("images") or []
                      if isinstance(i, dict)]
            if images:
                # Named, never carried: the executor's llm resolves each
                # under this call's own file grant (executor._llm_for).
                text = await context.llm(
                    params.get("messages") or [], params.get("max_tokens"),
                    images)
            else:
                text = await context.llm(
                    params.get("messages") or [], params.get("max_tokens")
                )
            return {"text": str(text),
                    "stop_reason": str(getattr(text, "stop_reason", "") or "")}

        if method == "ask":
            if context.ask is None:
                return {"answer": None}
            loop = asyncio.get_running_loop()
            started = loop.time()
            context.waiting += 1
            try:
                expects = str(params.get("expects") or "")
                answer = await context.ask(
                    str(params.get("question") or ""),
                    [str(c) for c in params.get("choices") or []],
                    **({"expects": expects} if expects else {}))
            finally:
                context.waiting -= 1
                context.waited += loop.time() - started
            return {"answer": answer}

        if method == "propose":
            if context.propose is None:
                return {"allowed": None}
            loop = asyncio.get_running_loop()
            started = loop.time()
            context.waiting += 1
            try:
                allowed = await context.propose(params.get("code"))
            finally:
                context.waiting -= 1
                context.waited += loop.time() - started
            return {"allowed": allowed}

        if method == "install":
            if context.install is None:
                raise WorkerError(
                    "Packages are installed only for a function that "
                    "declares `code: true` in its manifest.")
            # The platform's work, not the function's: a download is
            # not counted against the function's own clock.
            loop = asyncio.get_running_loop()
            started = loop.time()
            context.waiting += 1
            try:
                folder = await context.install(params.get("packages"))
            finally:
                context.waiting -= 1
                context.waited += loop.time() - started
            return {"folder": folder}

        if method == "credential":
            if context.credential is None:
                raise WorkerError(
                    "Logins are not available here — the function must "
                    "declare `credentials: true` in its manifest, and the "
                    "invocation must run inside a chat.")
            loop = asyncio.get_running_loop()
            started = loop.time()
            context.waiting += 1
            try:
                values = await context.credential(
                    str(params.get("host") or ""),
                    list(params.get("fields") or []),
                    str(params.get("account") or "") or None,
                    str(params.get("site") or "") or None,
                    bool(params.get("refresh")))
            finally:
                context.waiting -= 1
                context.waited += loop.time() - started
            return {"values": values}

        if method == "post":
            if context.post is None:
                return {"posted": False}
            return {"posted": bool(await context.post(
                str(params.get("text") or ""),
                [str(d) for d in params.get("displays") or []]))}

        if method == "show":
            if context.show is None:
                # Nobody could see it here: the
                # offer is not kept, and the function runs on.
                return {"display_id": None}
            spec = {key: value for key, value in params.items()
                    if key != "call_id"}
            return {"display_id": await context.show(spec)}

        kind, _, operation = method.partition(".")
        argument_names = RESOURCE_METHODS.get(operation)
        if kind != "resources" or argument_names is None:
            raise WorkerError(f"unknown ask '{method}'")

        answer = await getattr(context.resources, operation)(
            *(params.get(name) for name in argument_names)
        )
        if operation == "use_secret":
            # A credential may name the host it is for — a person's own
            # site — and the manifest said which field does. Handed
            # over, it is a host the worker may reach until this call
            # ends.
            place = getattr(context.handle, "place", None)
            if place is not None:
                context.learned.extend(place.learn(
                    str(params.get("resource_id") or ""), answer) or [])
        if operation in ("delete_data", "delete_file"):
            return {"deleted": bool(answer)}
        return answer

    async def _screen(self, handle: WorkerHandle, call_id: str, kind: str,
                      params: Dict[str, Any]) -> None:
        """A frame, or the end, of the screen one call shows — passed to
        the call's own sink, from the worker it was sent to and no
        other."""
        context = self._calls.get(call_id)
        if context is not None and context.handle is handle \
                and context.screen is not None:
            await self._as_invocation(context, context.screen(kind, params))

    async def screen_input(self, call_id: str, events: list) -> bool:
        """The person acting on a screen: to the worker running the
        call, as a notification. False when no such call runs."""
        context = self._calls.get(call_id)
        if context is None or context.handle is None:
            return False
        await context.handle.notify("screen.input", {
            "call_id": call_id, "events": list(events)})
        return True

    async def _progress(self, handle: WorkerHandle, call_id: str,
                        description: str) -> None:
        context = self._calls.get(call_id)
        if context is not None and context.handle is handle \
                and context.progress is not None:
            await self._as_invocation(context, context.progress(description))
