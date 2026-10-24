"""This Data Core process, as one replica among several
(docs/system/cluster.md).

    cluster = Cluster()          its id, its bus, what it sees of the others
    await cluster.start()        beating, reading the bus, standing for leader
    cluster.links()              the nodes linked to the other replicas
    cluster.leading              whether this replica runs the single loops

Every replica beats every two seconds, on a thread of its own: its own row, and the expiry of
every presence row it holds (database/stores/cluster.py) — its nodes'
links and its people's sockets. A replica that stops beating is gone ten
seconds later, and its rows with it; nodes and browsers reconnect to
another through the cluster's one address.

**The leader.** One replica at a time holds the leader's lease, a
conditional write renewed with each beat: it runs what must run once
for the whole cluster — lease fail-over, the clock, the outbound sweep —
and brings the schema to its version when it takes the lead. Another
takes the lease once it lapses. None of those loops is made safe by the
lease alone: each acts through a conditional write of its own (a
lease's number, a row's claim), so two replicas that both believe they
lead for a moment still fire nothing twice. The lease keeps them from
doing the same work N times over.

What another replica holds is only ever looked at, never trusted for a
decision: a node linked elsewhere is a place to send a chat, and the
replica holding it answers for it.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import uuid
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, List, Optional

from api.services.cluster.bus import Bus, BusError
from server.custom_logging import CustomLoggerFactory


class RemoteLink:
    """A node linked to another replica, as this one sees it: enough to
    place a chat there and to ask the node a question. Frames for it go
    to the replica that holds its link, which sends them."""

    open = True

    def __init__(self, row: Dict[str, Any], cluster: "Cluster"):
        self.cluster = cluster
        self.key = str(row["_id"])
        self.replica_id = str(row.get("replica_id") or "")
        self.identity = SimpleNamespace(
            key=self.key, name=str(row.get("name") or ""),
            authority_id=str(row.get("authority_id") or ""),
            scope=str(row.get("scope") or ""), shared=bool(row.get("shared")),
            labels=tuple(row.get("labels") or ()))
        self.connected_at = float(row.get("connected_at") or 0)
        self.chat_count = int(row.get("chats") or 0)
        self._load = int(row.get("load") or 0)

    @property
    def scope(self) -> str:
        return self.identity.scope

    @property
    def load(self) -> int:
        """What the node said of its load, and what its replica gave it:
        the larger of the two (api/services/nodes/link.py)."""
        return max(self._load, self.chat_count)

    async def ask(self, what: str, asked: Dict[str, Any],
                  timeout: float = 20.0) -> Dict[str, Any]:
        """A question for the node, asked by the replica holding its
        link. Raises RuntimeError with the node's words, or the bus's."""
        try:
            answer = await self.cluster.bus.call(
                self.replica_id, "ask",
                {"node": self.key, "what": what, "asked": asked,
                 "timeout": timeout}, timeout=timeout + 2.0)
        except BusError as exc:
            raise RuntimeError(str(exc))
        if answer.get("error"):
            raise RuntimeError(str(answer["error"])[:300])
        return answer.get("result") if isinstance(answer.get("result"), dict) else {}


class Cluster:
    #: How often a replica beats, and how long one that stops is still
    #: believed alive.
    HEARTBEAT_SECONDS = 2.0
    EXPIRES_SECONDS = 10.0
    #: How long the leader's lease lasts unless it is renewed.
    LEADER_SECONDS = 10.0
    #: How long a look at where a person's socket is, is kept.
    SOCKET_CACHE_SECONDS = 1.0

    def __init__(self, replica_id: Optional[str] = None):
        self.replica_id = replica_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self.bus = Bus(self.replica_id)
        self.leading = False
        #: called once each time this replica takes the lead
        self.on_lead: List[Callable[[], Awaitable[None]]] = []
        #: called with the ids of replicas that stopped beating
        self.on_gone: List[Callable[[set], Awaitable[None]]] = []
        #: called at the end of every beat (the links' rebalancing)
        self.on_tick: List[Callable[[], Awaitable[object]]] = []
        self._others: set = set()
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        #: what taking the lead started, held so the loop keeps it
        self._leading_work: set = set()
        self._warned_storage = False
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Beat once now — so this replica is seen, and leads at once
        where it is alone — then read the bus and go on beating, on a
        thread of its own: whether a replica is alive must not wait on
        how busy its loop is, whose every database call blocks it."""
        await self.tick()
        self.bus.start()
        self._loop = asyncio.get_running_loop()
        if self._thread is None or not self._thread.is_alive():
            self._stopping.clear()
            self._thread = threading.Thread(target=self._run, name="cluster-beat",
                                            daemon=True)
            self._thread.start()

    async def stop(self) -> None:
        """Stopping says so: the lead let go and the rows forgotten, so
        the others need not wait for them to lapse."""
        from database.stores.cluster import (
            LeaderLease, LinkPresenceStore, ReplicaStore, SocketPresenceStore)

        self._stopping.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            await asyncio.to_thread(thread.join, 5)
        self.bus.stop()
        try:
            if self.leading:
                LeaderLease().release(self.replica_id)
            self.leading = False
            LinkPresenceStore().forget_replica(self.replica_id)
            SocketPresenceStore().forget_replica(self.replica_id)
            ReplicaStore().gone(self.replica_id)
        except Exception as exc:
            self.logger.warning(f"Replica {self.replica_id} did not say it stopped: {exc}")

    def _run(self) -> None:
        pending = None
        while not self._stopping.wait(self.HEARTBEAT_SECONDS):
            try:
                beat = self.beat()
            except Exception as exc:  # one bad beat never ends the beating
                self.logger.warning(f"Replica heartbeat failed: {exc}")
                continue
            # What a beat calls for is the loop's; one at a time, so a
            # loop that is slow to get to them is not buried in them.
            if pending is None or pending.done():
                pending = asyncio.run_coroutine_threadsafe(
                    self._after(*beat), self._loop)

    async def tick(self) -> None:
        """One beat, and what it calls for: what the thread does every
        two seconds, for the start and a test to do at once."""
        await self._after(*await asyncio.to_thread(self.beat))

    def beat(self):
        """Alive, the rows held refreshed, the others looked at, and the
        leader's lease renewed or stood for. Returns (the replicas gone
        since the last beat, whether this one took the lead now)."""
        from database.mongo_db import load_database_schema
        from database.stores.cluster import (
            LeaderLease, LinkPresenceStore, ReplicaStore, SocketPresenceStore)

        ReplicaStore().beat(self.replica_id, self.EXPIRES_SECONDS, {
            "host": socket.gethostname(),
            "schema_version": int(load_database_schema().get("schema_version") or 0)})
        LinkPresenceStore().refresh(self.replica_id, self.EXPIRES_SECONDS)
        SocketPresenceStore().refresh(self.replica_id, self.EXPIRES_SECONDS)

        live = {str(row["_id"]) for row in ReplicaStore().live()} - {self.replica_id}
        gone, self._others = self._others - live, live
        if live:
            self._check_storage()

        was = self.leading
        self.leading = LeaderLease().claim(self.replica_id, self.LEADER_SECONDS) is not None
        if self.leading and not was:
            self.logger.info(f"Replica {self.replica_id} leads")
        elif was and not self.leading:
            self.logger.info(f"Replica {self.replica_id} no longer leads")
        return gone, self.leading and not was

    async def _after(self, gone: set, took_lead: bool) -> None:
        if gone:
            for callback in list(self.on_gone):
                await self._quietly(callback, gone)
        if took_lead:
            # Beside the beat, never in it: a schema's step can take
            # minutes.
            for callback in list(self.on_lead):
                task = asyncio.get_running_loop().create_task(self._quietly(callback))
                self._leading_work.add(task)
                task.add_done_callback(self._leading_work.discard)
        for callback in list(self.on_tick):
            await self._quietly(callback)

    async def _quietly(self, callback, *args) -> None:
        try:
            await callback(*args)
        except Exception as exc:
            self.logger.warning(f"A replica callback failed: {exc!r}")

    def _check_storage(self) -> None:
        """Files and packages on one replica's own disk are not the
        others': said once, loudly, where it is so."""
        from server.setup.app_state import get_settings

        if self._warned_storage:
            return
        try:
            provider = str(getattr(get_settings(), "file_storage_provider",
                                   "local") or "local").lower()
        except Exception:
            return
        if provider == "local":
            self._warned_storage = True
            self.logger.error(
                "Other Data Core replicas are running, and files and agent "
                "packages are kept on this replica's own disk "
                "(FILE_STORAGE_PROVIDER=local): a file uploaded to one is not "
                "on another unless every replica mounts the same volume. Use "
                "an object store (docs/run/deploying.md#a-cluster).")

    # ------------------------------------------------------------------
    # What the others hold
    # ------------------------------------------------------------------

    @property
    def others(self) -> set:
        """The other replicas alive at the last beat."""
        return set(self._others)

    def links(self) -> List[RemoteLink]:
        """Every node linked to another replica now. This replica's own
        links are its own NodeLinks', in memory, never read back."""
        from database.stores.cluster import LinkPresenceStore

        if not self._others and not self.bus.running:
            return []
        try:
            rows = LinkPresenceStore().live({"replica_id": {"$ne": self.replica_id}})
        except Exception as exc:
            self.logger.warning(f"The other replicas' links were not read: {exc}")
            return []
        return [RemoteLink(row, self) for row in rows]

    def link(self, node_key: str) -> Optional[RemoteLink]:
        """The node, where another replica holds its link."""
        from database.stores.cluster import LinkPresenceStore

        if not self._others and not self.bus.running:
            return None
        try:
            row = LinkPresenceStore().holder(node_key)
        except Exception:
            return None
        if row is None or row.get("replica_id") == self.replica_id:
            return None
        return RemoteLink(row, self)
