"""What the Data Core's replicas share (docs/system/cluster.md).

Several Data Core processes may serve one deployment, behind one
address and on one MongoDB replica set. Whatever one of them holds in
memory that another must know is written here, with an expiry, so a
replica that stops — killed, upgraded, lost — takes nothing with it
but the connections it held, and those reconnect to another.

    ReplicaStore         the replicas running now: each beats, and one
                         that stops beating is gone when its row lapses
    LinkPresenceStore    which replica holds each execution node's link,
                         and what the node last said of its load
    SocketPresenceStore  which replica holds each person's chat socket
    BusStore             the capped collection the replicas speak over
                         (api/services/cluster/bus.py)
    SharedGeneration     a number that moves on every change a cache
                         must hear of: sign-outs, policies, node
                         admissions; every replica reads it at most once
                         a second
    LeaderLease          which replica runs the loops one runner must:
                         lease fail-over, the clock, the outbound sweep,
                         the schema's steps
    RateCounter          a count in a window, kept as a conditional
                         increment, so every replica counts against one
                         limit

Every presence row carries its replica's id and an expiry its replica's
heartbeat moves on. A reader trusts a row only while it is unexpired;
the TTL index clears the rest. Nothing here decides who may do what:
leases, claims and approvals keep their own conditional writes, and a
row here only says where something is.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from database.stores.base import MongoStore
from util import utc_now


def _until(seconds: float):
    return utc_now() + timedelta(seconds=seconds)


class ReplicaStore(MongoStore):
    COLLECTION = "cluster_replicas"

    def beat(self, replica_id: str, seconds: float,
             about: Optional[Dict[str, Any]] = None) -> None:
        """This replica is alive for ``seconds`` more."""
        now = utc_now()
        self.col.update_one(
            {"_id": str(replica_id)},
            {"$set": {"beat_at": now, "expires_at": now + timedelta(seconds=seconds),
                      **{key: value for key, value in (about or {}).items()}},
             "$setOnInsert": {"started_at": now}},
            upsert=True)

    def live(self) -> List[Dict[str, Any]]:
        return list(self.col.find({"expires_at": {"$gt": utc_now()}}))

    def gone(self, replica_id: str) -> None:
        """A replica stopping says so, so the others need not wait for
        its row to lapse."""
        self.col.delete_one({"_id": str(replica_id)})


class _Presence(MongoStore):
    """Rows a replica holds, refreshed by its heartbeat."""

    def refresh(self, replica_id: str, seconds: float) -> None:
        self.col.update_many({"replica_id": str(replica_id)},
                             {"$set": {"expires_at": _until(seconds)}})

    def forget_replica(self, replica_id: str) -> None:
        self.col.delete_many({"replica_id": str(replica_id)})

    def live(self, query: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return list(self.col.find({**(query or {}),
                                   "expires_at": {"$gt": utc_now()}}))

    def holder(self, key: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({"_id": str(key),
                                  "expires_at": {"$gt": utc_now()}})


class LinkPresenceStore(_Presence):
    """One row per linked node: the replica its link is on. The node's
    link is one connection, so one row; a node linking again elsewhere
    takes the row, and the replica it left is told to close the old."""

    COLLECTION = "cluster_links"

    def hold(self, node_key: str, replica_id: str, link_id: str,
             about: Dict[str, Any], seconds: float) -> Optional[Dict[str, Any]]:
        """The node's link is on this replica now. Returns the row it
        replaced, when another replica held the node."""
        return self.col.find_one_and_update(
            {"_id": str(node_key)},
            {"$set": {"replica_id": str(replica_id), "link_id": str(link_id),
                      "expires_at": _until(seconds), **about}},
            upsert=True, return_document=ReturnDocument.BEFORE)

    def reported(self, node_key: str, link_id: str,
                 changes: Dict[str, Any]) -> None:
        self.col.update_one({"_id": str(node_key), "link_id": str(link_id)},
                            {"$set": changes})

    def release(self, node_key: str, link_id: str) -> None:
        """Only this link's row: a node that linked again elsewhere
        meanwhile keeps the row it has there."""
        self.col.delete_one({"_id": str(node_key), "link_id": str(link_id)})


class SocketPresenceStore(_Presence):
    """One row per person's chat socket: the replica it is on. A person
    has one socket per chat; a newer one, on any replica, replaces the
    older."""

    COLLECTION = "cluster_sockets"

    def hold(self, key: str, chat_id: str, replica_id: str, socket_id: str,
             seconds: float) -> Optional[Dict[str, Any]]:
        """Returns the row this socket replaced."""
        return self.col.find_one_and_update(
            {"_id": str(key)},
            {"$set": {"chat_id": str(chat_id), "replica_id": str(replica_id),
                      "socket_id": str(socket_id), "expires_at": _until(seconds)}},
            upsert=True, return_document=ReturnDocument.BEFORE)

    def release(self, key: str, socket_id: str) -> None:
        self.col.delete_one({"_id": str(key), "socket_id": str(socket_id)})

    def watched(self, chat_id: str) -> bool:
        return self.col.find_one({"chat_id": str(chat_id),
                                  "expires_at": {"$gt": utc_now()}},
                                 {"_id": 1}) is not None


class BusStore(MongoStore):
    """The replicas' messages: a capped collection, so it never grows,
    read through a change stream (api/services/cluster/bus.py)."""

    COLLECTION = "cluster_bus"

    def publish(self, message: Dict[str, Any]) -> None:
        self.col.insert_one(message)


class SharedGeneration(MongoStore):
    """Numbers per kind of change, moved by whichever replica made the
    change and read by every replica — the one read a cache makes to
    know it may still trust itself. A row holds a number per part, so
    a cache can drop only what a change touched (a session, against
    everything a person's access was resolved to)."""

    COLLECTION = "cluster_state"

    def __init__(self, name: str):
        super().__init__()
        self.name = f"generation:{name}"

    def bump(self, part: str = "number") -> Dict[str, int]:
        """One part moved. Returns every part as it stands now."""
        doc = self.col.find_one_and_update(
            {"_id": self.name}, {"$inc": {part: 1}},
            upsert=True, return_document=ReturnDocument.AFTER)
        return self._parts(doc)

    def read(self) -> Dict[str, int]:
        return self._parts(self.col.find_one({"_id": self.name}))

    @staticmethod
    def _parts(doc: Optional[Dict[str, Any]]) -> Dict[str, int]:
        return {key: int(value) for key, value in (doc or {}).items()
                if key != "_id" and isinstance(value, int)}


class LeaderLease(MongoStore):
    """One row: the replica that leads, a number, an expiry — the same
    conditional write as a chat's lease (``LeaseStore``). The holder
    renews it; another takes it only once it lapsed, and the number
    goes up with every new holder."""

    COLLECTION = "cluster_state"
    ID = "leader"

    def claim(self, replica_id: str, seconds: float) -> Optional[Dict[str, Any]]:
        """The lease, renewed or taken, or None while another holds it."""
        now = utc_now()
        until = now + timedelta(seconds=seconds)
        held = self.col.find_one_and_update(
            {"_id": self.ID, "holder": str(replica_id), "expires_at": {"$gt": now}},
            {"$set": {"expires_at": until}},
            return_document=ReturnDocument.AFTER)
        if held is not None:
            return held
        taken = self.col.find_one_and_update(
            {"_id": self.ID, "expires_at": {"$lte": now}},
            {"$set": {"holder": str(replica_id), "expires_at": until, "since": now},
             "$inc": {"number": 1}},
            return_document=ReturnDocument.AFTER)
        if taken is not None:
            return taken
        if self.col.find_one({"_id": self.ID}, {"_id": 1}) is not None:
            return None
        try:
            doc = {"_id": self.ID, "holder": str(replica_id), "expires_at": until,
                   "since": now, "number": 1}
            self.col.insert_one(doc)
            return doc
        except DuplicateKeyError:
            return None

    def release(self, replica_id: str) -> None:
        """A leader stopping lets go at once, so the next need not wait
        for the lease to lapse."""
        self.col.update_one({"_id": self.ID, "holder": str(replica_id)},
                            {"$set": {"expires_at": utc_now()}})

    def current(self) -> Optional[Dict[str, Any]]:
        doc = self.col.find_one({"_id": self.ID})
        if doc is None or doc.get("expires_at") is None \
                or doc["expires_at"] <= utc_now():
            return None
        return doc


class RateCounter(MongoStore):
    """A count in a window that every replica shares: one conditional
    increment per hit, on a row that expires with its window."""

    COLLECTION = "rate_counters"

    def hit(self, key: str, cap: int, seconds: float) -> bool:
        """One more, if the window has room. False when it is full."""
        now = utc_now()
        counted = self.col.find_one_and_update(
            {"_id": str(key), "expires_at": {"$gt": now}, "count": {"$lt": int(cap)}},
            {"$inc": {"count": 1}}, return_document=ReturnDocument.AFTER)
        if counted is not None:
            return True
        # Full, or no live window: a lapsed row starts a new one, and a
        # missing one is made. Either write holds only for one replica.
        fresh = {"count": 1, "expires_at": now + timedelta(seconds=seconds)}
        started = self.col.update_one(
            {"_id": str(key), "expires_at": {"$lte": now}}, {"$set": fresh})
        if started.modified_count == 1:
            return True
        try:
            self.col.insert_one({"_id": str(key), **fresh})
            return True
        except DuplicateKeyError:
            pass
        # Another replica started the window first: counted against it.
        return self.col.find_one_and_update(
            {"_id": str(key), "expires_at": {"$gt": now}, "count": {"$lt": int(cap)}},
            {"$inc": {"count": 1}}) is not None
