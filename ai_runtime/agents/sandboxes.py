"""Every Execution Sandbox registered with this AI Runtime, and which
one an agent runs on (docs/system/sandbox.md).

One AI Runtime serves many Execution Sandboxes, and one is the
ordinary case: the runtime holds no address of any. Each sandbox
connects to the runtime's registration port and registers, proving it
holds the secret set for the execution node (sandbox/wire.py);
says what it proved it holds a worker to, what it is given and uses,
its labels, the packages it holds and the agents it runs; and goes on
saying so every few seconds. A sandbox whose connection closed is
gone, and nothing more is placed on it.

    sandboxes = Sandboxes(secret, port=8005)
    await sandboxes.listen()                   registrations taken from here on
    await sandboxes.wait()                     at start, until one can run a worker
    sandbox = sandboxes.choose(agent, digest)  where an agent's next worker runs
    await sandboxes.drain(sandbox_id)          nothing new placed there

WHERE A WORKER RUNS (the plan's rule, section 1.8). Among the sandboxes
that are eligible — healthy, their locks proven by their own check in
the process that runs now, not draining, and holding every label asked
for — the one already running the agent; else one that already holds
its package; else the least loaded: the most memory left, then the
least of its processors in use. Sandboxes level on all of these are
taken in turn. What is close enough to count as level is a bucket of
memory and a tenth of the processors, so that a few megabytes of
difference do not decide.

What a person is shown — what agents use, what they keep, what they
did — is gathered from every sandbox behind the same calls one
sandbox answers (``usage``, ``files``, ``events``), so the monitoring
door (door/monitor.py) reads many as it read one.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from ai_runtime.agents.sandbox import ExecutionSandbox
from sandbox import wire
from sandbox.events import Events


class NoSandbox(RuntimeError):
    """No Execution Sandbox can take a worker now; the message says why."""


class Sandboxes:
    #: How long a sandbox may take to say who it is.
    REGISTER_SECONDS = 10
    #: How long the runtime waits, at its start, for a sandbox.
    WAIT_SECONDS = 120
    #: Memory differences smaller than this do not decide a placement.
    MEMORY_BUCKET = 256 * 1024 * 1024

    def __init__(self, secret: str = "", port: int = 8005, host: str = "0.0.0.0"):
        self.secret = str(secret or "")
        self.host = host
        self.port = int(port)
        self._sandboxes: Dict[str, Any] = {}
        #: Whose turn it is among sandboxes that are level.
        self._turn = 0
        #: digest -> the package's bytes, from the runtime's own cache
        #: (library.py): what a sandbox is sent when it asks.
        self.packages: Optional[Callable[[str], bytes]] = None
        #: Told of a sandbox that registered, and of one whose state
        #: changed — it drains, or is gone: (sandbox) -> None.
        self.registered: List[Callable[[Any], None]] = []
        self.changed: List[Callable[[Any], None]] = []
        #: Told what a check proved, when a sandbox is checked again.
        self.rechecked: List[Callable[[Dict[str, Any]], None]] = []
        self._server: Optional[asyncio.AbstractServer] = None
        self._serving: set = set()
        self.logger = logging.getLogger(self.__class__.__name__)

    @classmethod
    def of(cls, *sandboxes) -> "Sandboxes":
        """A registry of the sandboxes given, registered by hand: a
        test's, or a runtime's that names its one sandbox."""
        found = cls()
        for sandbox in sandboxes:
            found.add(sandbox)
        return found

    # ------------------------------------------------------------------
    # Who is registered
    # ------------------------------------------------------------------

    def add(self, sandbox) -> None:
        """A sandbox, registered: one that was before under its id is
        gone, replaced by this one — it started again."""
        before = self._sandboxes.get(sandbox.id)
        if before is not None and before is not sandbox:
            before.connected = False
        self._sandboxes[sandbox.id] = sandbox
        rechecked = getattr(sandbox, "rechecked", None)
        if isinstance(rechecked, list) and self._relay_recheck not in rechecked:
            rechecked.append(self._relay_recheck)

    def _relay_recheck(self, report: Dict[str, Any]) -> None:
        for listener in self.rechecked:
            listener(report)

    def remove(self, sandbox) -> None:
        """A sandbox, gone: nothing more is placed on it."""
        sandbox.connected = False
        if self._sandboxes.get(sandbox.id) is sandbox:
            del self._sandboxes[sandbox.id]
        self._tell(self.changed, sandbox)

    def all(self) -> List[Any]:
        return [self._sandboxes[name] for name in sorted(self._sandboxes)]

    def get(self, sandbox_id: str):
        return self._sandboxes.get(str(sandbox_id))

    def eligible(self, needs: Iterable[str] = ()) -> List[Any]:
        return [sandbox for sandbox in self.all() if sandbox.eligible(needs)]

    def problems(self, needs: Iterable[str] = ()) -> List[str]:
        """Why each sandbox that cannot take a worker cannot."""
        return [sandbox.why_not(needs) for sandbox in self.all()
                if not sandbox.eligible(needs)]

    def _tell(self, listeners, sandbox) -> None:
        for listener in list(listeners):
            try:
                listener(sandbox)
            except Exception as exc:          # a listener is housekeeping
                self.logger.warning(f"A listener failed: {exc}")

    # ------------------------------------------------------------------
    # Where a worker runs
    # ------------------------------------------------------------------

    def choose(self, agent: str = "", digest: str = "",
               needs: Iterable[str] = (), avoid: Iterable[str] = ()):
        """The sandbox an agent's next worker runs on, by the rule
        above. Raises NoSandbox, saying why none can."""
        needs = tuple(needs)
        avoided = {str(name) for name in avoid}
        candidates = [sandbox for sandbox in self.eligible(needs)
                      if sandbox.id not in avoided]
        if not candidates:
            why = [problem for problem in self.problems(needs) if problem]
            raise NoSandbox(
                "No Execution Sandbox can run it now"
                + (f": {'; '.join(why)}" if why else
                   ": none is registered with this AI Runtime"))
        running = [sandbox for sandbox in candidates
                   if agent and agent in sandbox.running]
        holding = [sandbox for sandbox in candidates
                   if digest and digest in sandbox.installed]
        candidates = running or holding or candidates
        best = min(self._load(sandbox) for sandbox in candidates)
        level = [sandbox for sandbox in candidates if self._load(sandbox) == best]
        chosen = level[self._turn % len(level)]
        self._turn += 1
        return chosen

    def _load(self, sandbox) -> tuple:
        """How loaded a sandbox is, as placement compares it: the
        memory it has left, the more the better, then the share of its
        processors in use, in buckets."""
        headroom = sandbox.headroom()
        memory = (-math.inf if headroom == math.inf
                  else -math.floor(headroom / self.MEMORY_BUCKET))
        return memory, math.floor(sandbox.processor() * 10)

    def any(self):
        """A sandbox for what is nobody's agent — a package verified,
        packages built — chosen as a worker's would be. Raises
        NoSandbox."""
        return self.choose()

    async def wait(self, seconds: Optional[float] = None) -> bool:
        """Whether a sandbox can take a worker, waited for: the runtime
        and its sandboxes start together, and the sandboxes register
        once the runtime is there. A sandbox given by hand is checked."""
        loop = asyncio.get_running_loop()
        until = loop.time() + (self.WAIT_SECONDS if seconds is None else seconds)
        while True:
            await self.check_unregistered()
            if self.eligible():
                return True
            if loop.time() >= until:
                for problem in self.problems() or [
                        "no Execution Sandbox registered with this AI Runtime"]:
                    self.logger.error(f"No worker can run: {problem}")
                return False
            await asyncio.sleep(0.5)

    async def check_unregistered(self) -> None:
        """Every sandbox given by hand that has not proved what it
        holds a worker to, asked to: a registered one said so itself."""
        for sandbox in self.all():
            if not getattr(sandbox, "registered", False) and not sandbox.locks:
                report = await sandbox.check()
                sandbox.locks = dict(report)

    # ------------------------------------------------------------------
    # Registrations
    # ------------------------------------------------------------------

    async def listen(self) -> None:
        """Take registrations from here on."""
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._register, self.host, self.port, limit=8 * 1024 * 1024)
        self.port = self._server.sockets[0].getsockname()[1]
        self.logger.info(
            f"Execution Sandboxes register with this AI Runtime on port {self.port}")

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
        # The registrations are hung up on first: a server waits for
        # every connection it took to close before it says it closed.
        for task in list(self._serving):
            task.cancel()
        if self._serving:
            await asyncio.wait(set(self._serving), timeout=5)
        if server is not None:
            try:
                await asyncio.wait_for(server.wait_closed(), 5)
            except asyncio.TimeoutError:
                pass

    async def _register(self, reader: asyncio.StreamReader,
                        writer: asyncio.StreamWriter) -> None:
        serving = asyncio.current_task()
        self._serving.add(serving)
        sandbox = None
        try:
            said = wire.challenge()
            writer.write(wire.line({"protocol": wire.PROTOCOL, "challenge": said}))
            await writer.drain()
            asked = wire.read(await asyncio.wait_for(
                reader.readline(), self.REGISTER_SECONDS))
            refusal = self._refusal(asked, said)
            if refusal:
                self.logger.warning(f"A registration was refused: {refusal}")
                writer.write(wire.line({"error": refusal}))
                await writer.drain()
                return
            sandbox = await self._admitted(asked, writer)
            if sandbox is None:
                return
            writer.write(wire.line({
                "registered": True,
                "proof": wire.proof(self.secret, wire.RUNTIME,
                                    str(asked.get("challenge") or ""))}))
            await writer.drain()
            self.add(sandbox)
            self._said_registered(sandbox)
            self._tell(self.registered, sandbox)
            while True:
                said_now = wire.read(await asyncio.wait_for(
                    reader.readline(), ExecutionSandbox.LATE_SECONDS * 3))
                if said_now is None:
                    break
                if said_now.get("op") == "heartbeat":
                    was = (sandbox.draining, sandbox.proven)
                    sandbox.learn(said_now)
                    if (sandbox.draining, sandbox.proven) != was:
                        self.logger.info(
                            f"Execution Sandbox {sandbox.id} "
                            + ("drains" if sandbox.draining else
                               "can take workers" if sandbox.proven else
                               "cannot prove what it holds a worker to"))
                        self._tell(self.changed, sandbox)
        except (OSError, ValueError, asyncio.TimeoutError,
                asyncio.IncompleteReadError):
            pass
        except asyncio.CancelledError:
            pass
        finally:
            self._serving.discard(serving)
            writer.close()
            if sandbox is not None and self._sandboxes.get(sandbox.id) is sandbox:
                self.logger.warning(
                    f"Execution Sandbox {sandbox.id} is gone: nothing more is "
                    f"placed on it.")
                self.remove(sandbox)

    def _refusal(self, asked: Optional[dict], said: str) -> str:
        if asked is None:
            return "it said nothing a registration is made of"
        mismatch = wire.mismatch(asked.get("protocol"))
        if mismatch:
            return mismatch
        sandbox_id = str(asked.get("sandbox") or "").strip()
        if asked.get("op") != "register" or not sandbox_id or len(sandbox_id) > 128:
            return "a registration names its sandbox"
        if not wire.proves(self.secret, wire.SANDBOX, said, asked.get("proof"),
                           sandbox_id):
            return (f"Execution Sandbox {sandbox_id} does not prove the "
                    f"execution node's secret ({wire.SECRET_SETTING})")
        return ""

    async def _admitted(self, asked: dict, writer: asyncio.StreamWriter):
        """The sandbox a registration names, reached where it says it
        answers, and saying it is the one that registered."""
        sandbox_id = str(asked["sandbox"]).strip()
        port = asked.get("port")
        address = str(asked.get("address") or "").strip()
        if not address:
            peer = writer.get_extra_info("peername") or ("", 0)
            address = f"{peer[0]}:{port}"
        try:
            sandbox = ExecutionSandbox(address, self.secret, sandbox_id,
                                       labels=asked.get("labels") or [])
        except ValueError as exc:
            writer.write(wire.line({"error": str(exc)}))
            await writer.drain()
            return None
        sandbox.registered = True
        sandbox.learn(asked)
        answer = await sandbox._answered({"op": "ping"})
        if answer.get("sandbox") != sandbox_id:
            problem = (f"Execution Sandbox {sandbox_id} registered and is not "
                       f"reached at {address}: "
                       f"{answer.get('error') or 'another sandbox answers there'}")
            self.logger.warning(problem)
            writer.write(wire.line({"error": problem}))
            await writer.drain()
            return None
        return sandbox

    def _said_registered(self, sandbox) -> None:
        holds = sandbox.locks
        if "error" in holds:
            self.logger.error(
                f"Execution Sandbox {sandbox.id} registered and cannot prove "
                f"it confines a worker; no worker starts there until it does: "
                f"{holds['error']}")
            return
        fences = holds.get("fences") or {}
        self.logger.info(
            f"Execution Sandbox {sandbox.id} registered at "
            f"{sandbox.host}:{sandbox.port}"
            + (f", labelled {', '.join(sorted(sandbox.labels))}" if sandbox.labels else "")
            + f": files {'fenced' if fences.get('files') else 'NOT fenced'}, "
            f"connections {'fenced' if holds.get('network_fenced') else 'NOT fenced'}, "
            f"system calls {'filtered' if holds.get('filters_calls') else 'NOT filtered'}"
            + (", draining" if sandbox.draining else ""))

    # ------------------------------------------------------------------
    # Draining
    # ------------------------------------------------------------------

    async def drain(self, sandbox_id: str, on: bool = True) -> bool:
        """No new worker placed on that sandbox, or placed there again;
        those running there finish what they are doing. Returns
        whether it drains."""
        sandbox = self.get(sandbox_id)
        if sandbox is None:
            raise NoSandbox(f"No Execution Sandbox {sandbox_id} is registered")
        draining = await sandbox.drain(on)
        self._tell(self.changed, sandbox)
        return draining

    # ------------------------------------------------------------------
    # What is shown, gathered from every sandbox
    # ------------------------------------------------------------------

    def _reached(self) -> List[Any]:
        return [sandbox for sandbox in self.all() if sandbox.connected]

    async def usage(self) -> Optional[Dict[str, Any]]:
        """What the sandboxes are given together and what each agent
        uses now, in the shape one sandbox says it — or None when none
        answered. Each agent says which sandbox it runs on, and
        ``sandboxes`` holds each sandbox's own numbers."""
        found = [(sandbox, said) for sandbox, said in zip(
            self._reached(), await asyncio.gather(
                *[sandbox.usage() for sandbox in self._reached()]))
            if isinstance(said, dict)]
        if not found:
            return None

        def total(values, all_known=True):
            known = [value for value in values if value is not None]
            if not known or (all_known and len(known) < len(values)):
                return None
            return sum(known)

        said = [message for _, message in found]
        return {
            "at": max(float(message.get("at") or 0) for message in said),
            "limits": {
                "memory": total([(m.get("limits") or {}).get("memory") for m in said]),
                "cpus": total([(m.get("limits") or {}).get("cpus") for m in said]),
            },
            "memory": total([m.get("memory") for m in said], all_known=False),
            "cpu": total([m.get("cpu") for m in said], all_known=False),
            "kernel_ended": sum(int(m.get("kernel_ended") or 0) for m in said),
            "left_behind": sum(int(m.get("left_behind") or 0) for m in said),
            "agents": [{**agent, "sandbox": agent.get("sandbox") or sandbox.id}
                       for sandbox, message in found
                       for agent in message.get("agents") or []
                       if isinstance(agent, dict)],
            "sandboxes": [{
                "sandbox": sandbox.id,
                "labels": sorted(sandbox.labels),
                "draining": sandbox.draining,
                "eligible": sandbox.eligible(),
                **{key: message.get(key) for key in
                   ("at", "limits", "memory", "cpu", "kernel_ended", "left_behind")},
            } for sandbox, message in found],
        }

    async def files(self, agent: str) -> Dict[str, Any]:
        """What the agent keeps in its home, in every sandbox it ran
        in; each file says which sandbox keeps it."""
        reached = self._reached()
        answers = await asyncio.gather(*[sandbox.files(agent) for sandbox in reached])
        listed = [(sandbox, answer) for sandbox, answer in zip(reached, answers)
                  if isinstance(answer, dict) and "error" not in answer]
        if not listed:
            errors = [str(answer.get("error")) for answer in answers
                      if isinstance(answer, dict) and answer.get("error")]
            refusals = [error for error in errors if "has not run" not in error]
            return {"error": (refusals or errors or [
                "That agent has no place here: it has not run."])[0]}
        return {
            "files": [{**entry, "sandbox": sandbox.id}
                      for sandbox, answer in listed
                      for entry in answer.get("files") or []],
            "count": sum(int(answer.get("count") or 0) for _, answer in listed),
            "bytes": sum(int(answer.get("bytes") or 0) for _, answer in listed),
        }

    async def events(self, limit: int = 200, kinds: Sequence[str] = (),
                     agent: str = "", before: Optional[float] = None) -> List[Dict[str, Any]]:
        """What every sandbox wrote down, the latest first; each event
        says which sandbox wrote it."""
        found: List[Dict[str, Any]] = []
        for answer in await asyncio.gather(*[
                sandbox.events(limit, kinds, agent, before)
                for sandbox in self._reached()]):
            found.extend(answer)
        found.sort(key=lambda event: event.get("at") or 0, reverse=True)
        return found[:max(1, min(int(limit or 0) or 200, Events.READ_MAX))]

    async def stop(self, agent: str) -> List[str]:
        """Every process of the agent's ended, in every sandbox."""
        errors: List[str] = []
        for answer in await asyncio.gather(*[
                sandbox.stop(agent) for sandbox in self._reached()]):
            errors.extend(answer)
        return errors

    async def keep(self, digests: Iterable[str]) -> int:
        """Every sandbox told which packages are still approved, to
        give up the rest."""
        digests = sorted({str(digest) for digest in digests if digest})
        if not digests:
            return 0
        removed = 0
        for answer in await asyncio.gather(*[
                sandbox.keep(digests) for sandbox in self._reached()
                if hasattr(sandbox, "keep")], return_exceptions=True):
            if isinstance(answer, int):
                removed += answer
        return removed

    async def check(self) -> Dict[str, Any]:
        """Every sandbox checked again; what the weakest holds, or the
        first error where none can prove it confines."""
        reports = [await sandbox.check() for sandbox in self._reached()]
        for sandbox, report in zip(self._reached(), reports):
            sandbox.locks = dict(report)
        good = [report for report in reports if "error" not in report]
        if not good:
            return reports[0] if reports else {
                "error": "No Execution Sandbox is registered with this AI Runtime"}
        return good[0]
