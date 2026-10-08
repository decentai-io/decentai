"""The agents a chat is served.

A chat's contract names its agents by approval: the ref everything
outside the package speaks, and the digest of the bytes to run. The
library (agents/library.py) holds packages by digest; this is what
stands between the contract and the library — serving what is here,
pulling and installing what is not, narrating the wait to whoever is
watching the chat, telling the platform when the code is ready, and
sweeping the disk of what nobody approves any more.

One per host (server/host.py), shared by every chat, because one
folder of packages serves every organization that approved those
bytes. The host asks it for a chat's roster when a session is built
or refreshed and when the clock fires for a chat; everything about
how a package gets here is this file's.
"""

import asyncio
import base64
from typing import Any, Callable, Dict, Optional

from ai_runtime.agents import ApprovedAgent, Confinement, InstalledAgent
from ai_runtime.agents.mcp import McpServer
from ai_runtime.agents.worker_pool import WorkerPool
from ai_runtime.runtime_logging import RuntimeLoggerFactory


class AgentRoster:
    def __init__(self, services, library, workers: WorkerPool, *,
                 relay: Callable, serving: Callable, logger=None):
        self.services = services
        self.library = library
        self.workers = workers
        #: async (chat_id, event) -> None — the host's socket to the
        #: chat's audience, for the narration of an install
        self.relay = relay
        #: () -> the rosters of every live session: what this process
        #: is serving from, which a sweep must keep
        self.serving = serving
        #: one materialization at a time per digest — two chats needing
        #: the same package install it once
        self._installs: Dict[str, asyncio.Lock] = {}
        #: digest -> why it would not materialize. The same bytes fail
        #: the same way; not tried again while the library holds them
        #: (a sweep forgets the refusal of bytes that are gone: reclaim).
        self._refused: Dict[str, str] = {}
        #: (ref, digest, state) already told to the platform. Once per
        #: process: the platform keeps the word, and a restart says it
        #: again, which costs one idempotent call per agent.
        self._reported: set = set()
        self.logger = logger or RuntimeLoggerFactory.get_logger(
            self.__class__.__name__)

    async def for_chat(self, chat_id: str,
                       contract: Dict[str, Any]) -> Dict[str, InstalledAgent]:
        """What the contract names, served: its agents by approval, and
        the person's MCP servers beside them."""
        return self.with_mcp(
            chat_id, await self.roster(chat_id, contract.get("agents")),
            contract.get("mcp"))

    async def roster(self, chat_id: str, entries) -> Dict[str, InstalledAgent]:
        """The contract's agents, served. ``None`` means everything
        loaded, by the package's own id — the sim's answer. A governing
        services names each agent by its approval: the ref everything
        outside the package speaks, and the digest to materialize it
        by. A plain id names an agent already here."""
        serving = {agent.agent_id: agent
                   for agent in self.library.loaded().values()}
        if entries is None:
            return serving
        roster: Dict[str, InstalledAgent] = {}
        for entry in entries or []:
            if isinstance(entry, str):
                if entry in serving:
                    roster[entry] = serving[entry]
                continue
            if not isinstance(entry, dict):
                continue
            ref = str(entry.get("agent_id") or "")
            digest = str(entry.get("package_digest") or "")
            if not ref or not digest:
                self.logger.warning(
                    f"Agent '{ref}' names no package; not served for "
                    f"{chat_id}")
                continue
            installed = await self.materialize(
                chat_id, ref, digest, str(entry.get("manifest_hash") or ""),
                name=str(entry.get("name") or ref))
            if installed is not None:
                roster[ref] = ApprovedAgent(installed, ref)
        return roster

    def with_mcp(self, chat_id: str, roster: Dict[str, InstalledAgent],
                  entries) -> Dict[str, InstalledAgent]:
        """The roster, with the person's MCP servers beside the agents
        (agents/mcp.py). Each asks the platform where it is when one of
        its tools is called, as this chat."""
        use = self.services.mcp_use
        root = chat_id.partition("/")[0]
        for entry in entries or []:
            ref = str(entry.get("ref") or "") if isinstance(entry, dict) else ""
            if ref and ref not in roster:
                roster[ref] = McpServer(
                    entry, lambda ref=ref: use(root, ref))
        return roster

    async def materialize(self, chat_id: str, ref: str, digest: str,
                           manifest_hash: str, name: str = "") -> Optional[InstalledAgent]:
        """The code an approval names, here — served from the library
        when it holds the digest, pulled and installed when it does
        not. A package that will not verify or load is logged,
        remembered as refused, and absent: the chat opens without it.

        The slow path is narrated to the attached audience, if any
        (``agent_status`` frames, docs/reference/session-door.md): a first open
        waits on pip, and a wait with no words reads as a fault."""
        try:
            installed = self.library.agent(digest)
        except ValueError as exc:
            # Not a digest at all: a fault in the approval's row. This
            # agent is absent, and the chat opens with the others.
            self.logger.error(f"Agent {ref} absent for {chat_id}: {exc}")
            return None
        if installed is not None:
            # Already serving — built by an earlier chat, or by the
            # warm-up, which had no delegation to say so with.
            self._report(chat_id, ref, digest, "ready")
            return installed
        if digest in self._refused:
            # Logged every time rather than once: an agent the chat cannot
            # see is the symptom an administrator has to explain, and without
            # this the only trace of why is one line from whenever it
            # first happened.
            self.logger.warning(
                f"Agent {ref} absent for {chat_id}: {digest[:19]}… was "
                f"refused ({self._refused[digest]})")
            return None
        name = name or ref
        lock = self._installs.setdefault(digest, asyncio.Lock())
        async with lock:
            installed = self.library.agent(digest)
            if installed is not None or digest in self._refused:
                return installed
            arrived = False
            try:
                archive = None
                if not self.library.has(digest):
                    await self._status(chat_id, ref, name, "pulling",
                                       f"Preparing {name}: fetching the "
                                       f"approved package…")
                    archive = await self._pull(chat_id, ref)
                arrived = True
                await self._status(chat_id, ref, name, "installing",
                                   f"Preparing {name}: building its "
                                   f"environment — the first time takes "
                                   f"a minute…")
                # The venv build (pip) and the probe worker run in a
                # thread: the loop's other chats and the clock stay
                # awake while one package installs.
                installed = await asyncio.to_thread(
                    self.library.install, digest, archive, manifest_hash)
            except Exception as exc:
                # A refusal remembers BYTES THAT WOULD NOT INSTALL,
                # because those fail the same way every time and each
                # retry costs a pip build. Bytes that never arrived say
                # nothing about the package: a delegation the platform
                # would not accept, a gateway that timed out, a pull
                # door that was briefly unreachable. Remembering one of
                # those would hide the agent from every chat for as long
                # as the refusal is kept — a blip, promoted to an outage,
                # in silence. The next chat asks again; a fetch that
                # fails again costs one request.
                if arrived:
                    self._refused[digest] = str(exc)
                self.logger.error(
                    f"Agent {ref} not materialized ({digest[:19]}…): {exc}"
                    + ("" if arrived else " — the package never arrived, "
                                          "so the next chat asks again"))
                await self._status(chat_id, ref, name, "failed",
                                   f"{name} could not be prepared: {exc}")
                if arrived:
                    self._report(chat_id, ref, digest, "failed", str(exc))
                return None
            self.logger.info(
                f"Agent {ref} serving as {digest[:19]}… (pulled for {chat_id})")
            await self._status(chat_id, ref, name, "ready",
                               f"{name} is ready.")
            self._report(chat_id, ref, digest, "ready")
        # An install is the one moment this process knows the disk grew,
        # and a chat is connected, so there is a delegation to ask with.
        # Outside the lock: reclaiming is housekeeping, and nothing else
        # should wait behind it.
        try:
            await self.reclaim(chat_id)
        except Exception as exc:
            self.logger.warning(f"Nothing reclaimed after an install: {exc}")
        return installed

    async def reclaim(self, chat_id: str) -> int:
        """Delete the code and environments nobody approves any more.

        The platform says which digests are still pinned — anywhere, by
        anyone, because one folder here serves every organization that
        approved those bytes — and this process adds what it is using:
        the roster of every live session, and every running worker. What
        is on disk outside those two sets is nobody's, and goes with its
        virtual environment.

        Housekeeping, so it never fails anything: a platform that will
        not answer means nothing is deleted, which is the safe way to be
        wrong."""
        stored = getattr(self.library, "stored", None)
        # A library that cannot say what is on disk (the plain mapping a
        # test passes for one) has nothing to sweep. Housekeeping must
        # never break serving.
        if stored is None:
            return 0
        try:
            keep = set(await self.services.pinned_digests(chat_id))
        except Exception as exc:
            self.logger.warning(f"Pinned digests not read ({exc}); "
                                f"nothing reclaimed")
            return 0
        # An empty answer is not permission to empty the disk: a platform
        # that has forgotten every approval is a platform to distrust.
        if not keep:
            return 0

        keep |= self._in_use()
        removed = 0
        for digest in stored():
            if digest in keep:
                continue
            try:
                # Deleting a package and its environment is the disk's
                # time, and not the loop's.
                await asyncio.to_thread(self.library.forget, digest)
            except Exception as exc:
                self.logger.warning(
                    f"{digest[:19]}… not reclaimed: {exc}")
                continue
            removed += 1
        # A refusal remembers BYTES, and these bytes are not here: either
        # this sweep just deleted them, or they never landed at all —
        # tampered ones are rolled back before they reach the disk, and
        # their entry would otherwise outlive every reason for it, which
        # is the "unavailable until a restart" trap. Anything the library
        # still holds keeps its refusal: that package is unchanged.
        for digest in list(self._refused):
            try:
                held = self.library.has(digest)
            except ValueError:
                held = False
            if not held:
                self._refused.pop(digest, None)
        if removed:
            self.logger.info(f"Reclaimed {removed} unapproved package(s)")
        return removed

    def _in_use(self) -> set:
        """Digests this process is serving from: what every live session
        may call, and what every worker is running. A session holds its
        roster for as long as it lives, so its code must outlive the
        sweep whether or not a call is in flight."""
        digests = {
            getattr(agent, "digest", "")
            for roster in self.serving()
            for agent in roster.values()
        }
        digests |= self.workers.live_digests()
        return {digest for digest in digests if digest}

    def _report(self, chat_id: str, ref: str, digest: str, state: str,
                error: str = "") -> None:
        """Tell the platform, once per process, that this agent's code
        is ready here — or would not build — which is what the Agents
        page shows as Preparing turning into Ready. On a background
        task as this chat's delegation: no session waits on it, and a
        platform that keeps nothing loses only the word. The socket
        narration (`_status`) is for whoever is watching THIS chat;
        this is for the person who clicked Install and is still on the
        Agents page."""
        report = self.services.agent_prepared
        key = (ref, digest, state)
        if key in self._reported:
            return
        self._reported.add(key)

        async def send():
            try:
                # With it, what this process holds the agent to: the
                # agent's page says which part of that is not enforced.
                await report(chat_id, ref, digest, state, error,
                             confined=Confinement.report())
            except Exception as exc:
                self._reported.discard(key)
                self.logger.warning(f"Readiness of {ref} not reported: {exc}")

        asyncio.get_running_loop().create_task(send())

    async def _status(self, chat_id: str, ref: str, name: str,
                      phase: str, text: str) -> None:
        """What the wait is, to whoever is watching. On the socket
        alone: a courtesy to the audience, never a record — the
        durable story of an install is the platform's audit."""
        await self.relay(chat_id, {
            "event": "agent_status", "chat_id": chat_id, "agent": ref,
            "name": name, "phase": phase, "text": text,
        })

    async def _pull(self, chat_id: str, ref: str) -> bytes:
        """The approved bytes, through the services' pull door, as this
        chat's delegation. The digest they must hash to came with the
        contract; the library verifies before it writes."""
        answer = await self.services.fetch_package(chat_id, ref)
        encoded = str((answer or {}).get("package") or "")
        if not encoded:
            raise RuntimeError("the pull door answered with no package")
        return base64.b64decode(encoded)
