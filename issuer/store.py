"""What the issuer remembers, in a MongoDB database of its own that its instances share
(documentation/communication.md): tokens, the certificates it signed, refused ids."""

import hashlib
import secrets
import time
from typing import Any

from pymongo import MongoClient, ReturnDocument


class Store:
    def __init__(self, uri: str, database: str = "decentai_issuer"):
        self._client = MongoClient(uri)
        db = self._client[database]
        self.tokens, self.nodes = db["tokens"], db["nodes"]
        self.tokens.create_index("hash", unique=True)

    def close(self) -> None:
        self._client.close()

    # Tokens

    def create_token(self, kind: str, metadata: dict[str, Any], lifetime: int, uses: int | None,
                     expires: float | None, made_by: str) -> tuple[str, dict[str, Any]]:
        """A new token: its secret (shown once, kept only as a hash) and its record."""
        secret = f"dct_{secrets.token_urlsafe(24)}"
        token = {"_id": f"tok_{secrets.token_hex(6)}", "hash": self.hash(secret), "kind": kind,
                 "metadata": metadata, "lifetime": lifetime, "uses": uses, "expires": expires,
                 "withdrawn": False, "made_by": made_by, "made": time.time()}
        self.tokens.insert_one(token)
        return secret, token

    def spend_token(self, secret: str) -> dict[str, Any] | None:
        """The token for this secret, one use spent, if it may still enroll: not withdrawn, not
        expired, not used up. In one step, so two nodes can't both take its last use."""
        now = time.time()
        return self.tokens.find_one_and_update(
            {"hash": self.hash(secret), "withdrawn": False,
             "$and": [{"$or": [{"expires": None}, {"expires": {"$gt": now}}]},
                      {"$or": [{"uses": None}, {"uses": {"$gt": 0}}]}]},
            [{"$set": {"uses": {"$cond": [{"$eq": ["$uses", None]}, None, {"$subtract": ["$uses", 1]}]}}}],
            return_document=ReturnDocument.AFTER)

    def give_back(self, token_id: str) -> None:
        """The use ``spend_token`` took, for an enrollment that failed."""
        self.tokens.update_one({"_id": token_id, "uses": {"$ne": None}}, {"$inc": {"uses": 1}})

    def token(self, token_id: str) -> dict[str, Any] | None:
        return self.tokens.find_one({"_id": token_id})

    def withdraw_token(self, token_id: str) -> bool:
        return self.tokens.update_one({"_id": token_id}, {"$set": {"withdrawn": True}}).matched_count == 1

    def list_tokens(self) -> list[dict[str, Any]]:
        return list(self.tokens.find({}, {"hash": 0}).sort("made", -1))

    # Nodes: one record per id, updated on every renewal

    def record(self, node_id: str, kind: str, metadata: dict[str, Any], token_id: str | None,
               issued: int, expires: int) -> None:
        self.nodes.update_one({"_id": node_id}, {
            "$set": {"kind": kind, "metadata": metadata, "issued": issued, "expires": expires},
            "$setOnInsert": {"token": token_id, "launched_by": metadata.get("launched_by"),
                             "refused": False, "enrolled": issued}}, upsert=True)

    def node(self, node_id: str) -> dict[str, Any] | None:
        return self.nodes.find_one({"_id": node_id})

    def refuse(self, node_id: str) -> bool:
        return self.nodes.update_one({"_id": node_id}, {"$set": {"refused": True}}).matched_count == 1

    def allow(self, node_id: str) -> bool:
        return self.nodes.update_one({"_id": node_id}, {"$set": {"refused": False}}).matched_count == 1

    def list_nodes(self) -> list[dict[str, Any]]:
        return list(self.nodes.find({}).sort("enrolled", -1))

    @staticmethod
    def hash(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()
