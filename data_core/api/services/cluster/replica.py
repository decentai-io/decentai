"""This Data Core process, as one replica among several
(docs/system/cluster.md).

    cluster = Cluster()          its id, its bus, what it sees of the others
    await cluster.start()        beating, reading the bus, standing for leader
    cluster.links()              the nodes linked to the other replicas
    cluster.leading              whether this replica runs the single loops

Every replica beats every two seconds: its own row, and the expiry of
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
        self._others: set = set()
        self._task: Optional[asyncio.Task] = None
        self._warned_storage = False
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Beat once now — so this replica is seen, and leads at once
        where it is alone — then read the bus and go on beating."""
        await self.tick()
        self.bus.start()
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        """Stopping says so: the lead let go and the rows forgotten, so
        the others need not wait for them to lapse."""
        from database.stores.cluster import (
            LeaderLease, LinkPresenceStore, ReplicaStore, SocketPresenceStore)

        task, self._task = self._task, None
        if task is not None:
            task.cancel()
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

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.HEARTBEAT_SECONDS)
            try:
                await self.tick()
            except Exception as exc:  # one bad beat never ends the loop
                self.logger.warning(f"Replica heartbeat failed: {exc}")

    async def tick(self) -> None:
        """One beat: alive, the rows held refreshed, the others looked
        at, and the leader's lease renewed or stood for."""
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
        if gone:
            for callback in list(self.on_gone):
                await self._quietly(callback, gone)

        was = self.leading
        self.leading = LeaderLease().claim(self.replica_id, self.LEADER_SECONDS) is not None
        if self.leading and not was:
            self.logger.info(f"Replica {self.replica_id} leads")
            for callback in list(self.on_lead):
                await self._quietly(callback)
        elif was and not self.leading:
            self.logger.info(f"Replica {self.replica_id} no longer leads")

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
