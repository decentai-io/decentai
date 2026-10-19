"""Execution nodes, as the Data Core keeps them
(docs/system/execution-nodes.md).

    AuthorityStore   who may vouch for a node: a public key, or an
                     issuer's published key set, for the whole
                     deployment (``shared``, the operator's) or for one
                     organization (its administrators')
    NodeStore        the nodes that were admitted, kept from what each
                     presented and reported: a view for administrators,
                     never a list anybody fills in
    RefusedNodeStore single nodes refused by name, for the urgent case
                     a credential's expiry is too slow for

A node is never registered. Trust is given to an authority, once, and
any node holding a credential that authority signed is admitted
(api/services/nodes/admission.py). A node is known by its authority and
the name its credential gives it together, so two authorities naming a
node alike name two nodes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.stores.base import MongoStore
from util import iso, new_id, utc_now


class AuthorityStore(MongoStore):
    COLLECTION = "node_authorities"

    #: The scope of an authority the operator registers: its nodes may
    #: serve every organization that does not keep to its own.
    SHARED = "shared"
    KIND_KEY = "key"
    KIND_ISSUER = "issuer"

    @staticmethod
    def fingerprint(public_key: str) -> str:
        """A public key's name (contracts/node.py), which a credential's
        ``kid`` may say."""
        from contracts.node import key_fingerprint

        return key_fingerprint(public_key)

    def register(self, *, name: str, scope: str, by: Dict[str, Any],
                 public_key: str = "", jwks_url: str = "",
                 issuer: str = "") -> Dict[str, Any]:
        """A new authority. Exactly one of ``public_key`` (PEM) or
        ``jwks_url`` (an issuer's published key set, with the ``issuer``
        its tokens name). Raises ValueError with the sentence to show."""
        name = self._clean_name(name, "An authority's name")
        scope = str(scope or "").strip()
        if not scope:
            raise ValueError("An authority's scope is shared or one organization.")
        public_key = str(public_key or "").strip().replace("\\n", "\n")
        jwks_url = str(jwks_url or "").strip()
        issuer = str(issuer or "").strip()
        if bool(public_key) == bool(jwks_url):
            raise ValueError(
                "An authority is a public key or an issuer's key-set address, "
                "one of the two.")
        doc: Dict[str, Any] = {
            "_id": f"auth_{new_id()[:16]}",
            "name": name,
            "scope": scope,
            "registered_by": {
                "user_id": str(by.get("user_id") or ""),
                "email": str(by.get("email") or ""),
                "org_id": str(by.get("org_id") or ""),
            },
            "registered_at": utc_now(),
            "revoked_at": None,
        }
        if public_key:
            try:
                doc["fingerprint"] = self.fingerprint(public_key)
            except Exception:
                raise ValueError("That is not a public key in PEM form.")
            if self.col.find_one({"fingerprint": doc["fingerprint"],
                                  "revoked_at": None}):
                raise ValueError("That key is registered already.")
            doc.update(kind=self.KIND_KEY, public_key=public_key)
        else:
            if not jwks_url.startswith(("https://", "http://")):
                raise ValueError("An issuer's key set is an https:// address.")
            if not issuer:
                raise ValueError(
                    "Say the issuer its tokens name (iss), with the key set.")
            doc.update(kind=self.KIND_ISSUER, jwks_url=jwks_url, issuer=issuer)
        self.col.insert_one(doc)
        return doc

    def by_fingerprint(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        """The authority holding this key, revoked or not: a key that was
        revoked once is not registered again by a start."""
        return self.col.find_one({"fingerprint": fingerprint})

    def live(self) -> List[Dict[str, Any]]:
        """Every authority not revoked."""
        return list(self.col.find({"revoked_at": None}))

    def list(self, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        query = {} if scope is None else {"scope": scope}
        return list(self.col.find(query).sort("registered_at", 1))

    def revoke(self, authority_id: str, by: Dict[str, Any]) -> bool:
        changed = self.col.update_one(
            {"_id": str(authority_id or ""), "revoked_at": None},
            {"$set": {"revoked_at": utc_now(),
                      "revoked_by": str(by.get("email") or by.get("user_id") or "")}})
        return changed.modified_count == 1

    @staticmethod
    def to_public(doc: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "authority_id": doc["_id"],
            "name": doc.get("name", ""),
            "kind": doc.get("kind", ""),
            "scope": doc.get("scope", ""),
            "fingerprint": doc.get("fingerprint", ""),
            "jwks_url": doc.get("jwks_url", ""),
            "issuer": doc.get("issuer", ""),
            "registered_by": dict(doc.get("registered_by") or {}),
            "registered_at": iso(doc.get("registered_at")),
            "revoked": doc.get("revoked_at") is not None,
            "revoked_at": iso(doc.get("revoked_at")),
        }


class NodeStore(MongoStore):
    """What admitted nodes presented and reported. Written by the Data
    Core alone, as nodes come and go; read by administrators."""

    COLLECTION = "execution_nodes"

    #: What of a node's own report is kept. Shown, never a gate: a node
    #: can say anything about itself.
    REPORT_KEYS = ("version", "protocol_version", "capacity", "load",
                   "labels", "locks", "sandboxes", "started_at")

    def seen(self, identity, connected: Optional[bool] = None) -> None:
        """An admitted node presented itself: kept, or brought up to date."""
        now = utc_now()
        changes: Dict[str, Any] = {
            "name": identity.name,
            "authority_id": identity.authority_id,
            "scope": identity.scope,
            "labels": list(identity.labels),
            "last_seen": now,
        }
        if connected is not None:
            changes["connected"] = bool(connected)
        self.col.update_one(
            {"_id": identity.key},
            {"$set": changes, "$setOnInsert": {"first_seen": now}},
            upsert=True)

    def reported(self, key: str, report: Dict[str, Any]) -> None:
        """What the node says of itself, as it said it."""
        kept = {name: report[name] for name in self.REPORT_KEYS
                if name in (report or {})}
        self.col.update_one(
            {"_id": key},
            {"$set": {"reports": kept, "last_seen": utc_now()}})

    def disconnected(self, key: str) -> None:
        self.col.update_one({"_id": key}, {"$set": {"connected": False}})

    def list(self, scopes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        query = {} if scopes is None else {"scope": {"$in": list(scopes)}}
        return list(self.col.find(query).sort("first_seen", 1))

    @staticmethod
    def to_public(doc: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "node_id": doc["_id"],
            "name": doc.get("name", ""),
            "authority_id": doc.get("authority_id", ""),
            "scope": doc.get("scope", ""),
            "labels": list(doc.get("labels") or []),
            "connected": bool(doc.get("connected")),
            "first_seen": iso(doc.get("first_seen")),
            "last_seen": iso(doc.get("last_seen")),
            "reports": dict(doc.get("reports") or {}),
        }


class LeaseStore(MongoStore):
    """Which execution node holds each active chat
    (docs/system/execution-nodes.md#leases).

    One document per chat: the node, a number, an expiry. The number
    goes up every time the chat is given to a node anew, and never down,
    so a write that carries an older number is from a node that no
    longer holds the chat, and is refused (server/routes/app.py). The
    expiry is what a node that stops answering loses the chat by:
    renewed while it lives, gone a minute after it does not.

    Durable, so a Data Core that restarts still refuses an old number."""

    COLLECTION = "chat_leases"
    #: How long a lease lasts unless it is renewed, and how often a node
    #: renews (decided: 60 and 20).
    SECONDS = 60
    RENEW_SECONDS = 20

    @staticmethod
    def live(doc: Optional[Dict[str, Any]], now=None) -> bool:
        if not doc or doc.get("released"):
            return False
        expires = doc.get("expires_at")
        return expires is not None and expires > (now or utc_now())

    def current(self, chat_id: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({"_id": str(chat_id)})

    def grant(self, chat_id: str, node_id: str, owner: Dict[str, Any],
              seconds: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """The chat for this node: the lease it already holds, renewed,
        or a new one numbered past every lease before — or None while
        another node's lease is live."""
        from datetime import timedelta

        from pymongo import ReturnDocument
        from pymongo.errors import DuplicateKeyError

        now = utc_now()
        until = now + timedelta(seconds=seconds or self.SECONDS)
        chat_id, node_id = str(chat_id), str(node_id)
        held = self.col.find_one_and_update(
            {"_id": chat_id, "node_id": node_id, "released": False,
             "expires_at": {"$gt": now}},
            {"$set": {"expires_at": until}},
            return_document=ReturnDocument.AFTER)
        if held is not None:
            return held
        fresh = {"node_id": node_id, "expires_at": until, "granted_at": now,
                 "released": False,
                 "org_id": str(owner.get("org_id") or ""),
                 "user_id": str(owner.get("user_id") or "")}
        taken = self.col.find_one_and_update(
            {"_id": chat_id, "$or": [{"released": True},
                                     {"expires_at": {"$lte": now}}]},
            {"$set": fresh, "$inc": {"number": 1}},
            return_document=ReturnDocument.AFTER)
        if taken is not None:
            return taken
        if self.col.find_one({"_id": chat_id}, {"_id": 1}) is not None:
            return None  # another node holds it, and lives
        try:
            doc = {"_id": chat_id, "number": 1, **fresh}
            self.col.insert_one(doc)
            return doc
        except DuplicateKeyError:
            return None

    def renew(self, node_id: str, chats) -> int:
        """A node's heartbeat: every lease it says it holds and does
        hold, live again for a minute."""
        from datetime import timedelta

        now = utc_now()
        ids = [str(chat_id) for chat_id in chats or []]
        if not ids:
            return 0
        return self.col.update_many(
            {"_id": {"$in": ids}, "node_id": str(node_id), "released": False,
             "expires_at": {"$gt": now}},
            {"$set": {"expires_at": now + timedelta(seconds=self.SECONDS)}},
        ).modified_count

    def holds(self, chat_id: str, node_id: str, number: Any) -> bool:
        """Whether this node holds this chat under this number now."""
        try:
            number = int(number)
        except (TypeError, ValueError):
            return False
        doc = self.current(chat_id)
        return (self.live(doc) and doc.get("node_id") == str(node_id)
                and int(doc.get("number") or 0) == number)

    def release(self, chat_id: str, node_id: str = "",
                number: Optional[int] = None) -> bool:
        """The chat is no node's now. The node and the number stay on
        the record: the next placement prefers the last holder, and
        the next number is past this one."""
        query: Dict[str, Any] = {"_id": str(chat_id), "released": False}
        if node_id:
            query["node_id"] = str(node_id)
        if number is not None:
            query["number"] = int(number)
        return self.col.update_one(
            query, {"$set": {"released": True, "expires_at": utc_now()}}
        ).modified_count == 1

    def lapsed(self) -> List[Dict[str, Any]]:
        """Leases whose node stopped renewing them and never said it let
        go: the chats to place again."""
        return list(self.col.find({"released": False,
                                   "expires_at": {"$lte": utc_now()}}))

    def of_node(self, node_id: str) -> List[Dict[str, Any]]:
        return [doc for doc in self.col.find({"node_id": str(node_id)})
                if self.live(doc)]


class RefusedNodeStore(MongoStore):
    """Nodes refused by name: the urgent case. The usual way out is the
    credential's own expiry, or revoking the authority that vouches."""

    COLLECTION = "refused_nodes"

    def refuse(self, key: str, by: Dict[str, Any], reason: str = "") -> None:
        self.col.update_one(
            {"_id": str(key)},
            {"$set": {"refused_at": utc_now(),
                      "refused_by": str(by.get("email") or by.get("user_id") or ""),
                      "org_id": str(by.get("org_id") or ""),
                      "reason": str(reason or "")[:300]}},
            upsert=True)

    def allow(self, key: str) -> bool:
        return self.col.delete_one({"_id": str(key)}).deleted_count == 1

    def keys(self) -> set:
        return {doc["_id"] for doc in self.col.find({}, {"_id": 1})}
