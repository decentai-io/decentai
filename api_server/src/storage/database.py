"""The database: its connection, every collection's indexes, and the data's version (api-server.md §11)."""

from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.database import Database as MongoDatabase
from pymongo.errors import DuplicateKeyError

log = logging.getLogger("api_server")


class Database:
    """One connection for the process. Every store's indexes are made at start; the data's version is checked."""

    #: The version of the data this code writes. An upgrade that changes the data adds a step to STEPS.
    VERSION = 2
    #: 2: a chat's empty choice of agents, skills or MCP servers meant all; all is null now, and empty is none.
    STEPS: dict[int, Any] = {2: lambda db: [db["chats"].update_many({f"settings.{name}": []},
                                                                    {"$set": {f"settings.{name}": None}})
                                            for name in ("agents", "skills", "mcp_servers")]}
    DEPLOYMENT = "deployment"
    client: MongoClient | None = None
    db: MongoDatabase | None = None

    @classmethod
    def connect(cls, uri: str, name: str) -> None:
        cls.client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=10000)
        cls.db = cls.client[name]

    @classmethod
    def prepare(cls) -> None:
        """Indexes made (made again, they change nothing), then the data brought to this version, once."""
        for store in cls.stores():
            for keys, options in store.INDEXES:
                cls.db[store.COLLECTION].create_index(keys, **options)
        deployment = cls.db[cls.DEPLOYMENT]
        deployment.update_one({"_id": cls.DEPLOYMENT}, {"$setOnInsert": {"data_version": cls.VERSION}}, upsert=True)
        found = deployment.find_one({"_id": cls.DEPLOYMENT})["data_version"]
        if found > cls.VERSION:
            raise RuntimeError(f"The data is at version {found}; this server knows {cls.VERSION}.")
        while found < cls.VERSION:
            if Lease("upgrade", 600).take():
                cls.STEPS[found + 1](cls.db)
                deployment.update_one({"_id": cls.DEPLOYMENT}, {"$set": {"data_version": found + 1}})
                log.info(f"The data is now at version {found + 1}.")
            time.sleep(1)
            found = deployment.find_one({"_id": cls.DEPLOYMENT})["data_version"]

    @classmethod
    def ready(cls) -> bool:
        try:
            cls.client.admin.command("ping")
            return cls.db[cls.DEPLOYMENT].find_one({"_id": cls.DEPLOYMENT})["data_version"] == cls.VERSION
        except Exception:
            return False

    @classmethod
    def stores(cls, base: type | None = None) -> list[type]:
        """Every store whose module is loaded."""
        found = []
        for store in (base or Store).__subclasses__():
            if store.COLLECTION:
                found.append(store)
            found += cls.stores(store)
        return found


class Store:
    """A collection, with what every store does the same way."""

    COLLECTION = ""
    #: Each (keys, options), as pymongo's create_index takes them.
    INDEXES: tuple = ()
    PAGE = 100
    ASC, DESC = ASCENDING, DESCENDING

    @property
    def col(self):
        return Database.db[self.COLLECTION]

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def new_id() -> str:
        """Unguessable, and in the order made."""
        return f"{time.time_ns() // 1000:014x}{secrets.token_hex(8)}"

    @staticmethod
    def expires(seconds: float) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)

    def get_in(self, org_id: str, doc_id: Any) -> dict[str, Any] | None:
        return self.col.find_one({"_id": str(doc_id or ""), "org_id": str(org_id or "")})

    @classmethod
    def size(cls, limit: Any) -> int:
        """A page's size: the limit asked, from 1 to PAGE."""
        return max(1, min(int(limit) if str(limit).isdigit() else cls.PAGE, cls.PAGE))

    def page(self, query: dict[str, Any], after: Any = "", limit: Any = PAGE, newest: bool = False) -> list[dict]:
        """At most PAGE documents, after the id given, oldest or newest first."""
        size = self.size(limit)
        if after:
            query = {**query, "_id": {"$lt" if newest else "$gt": str(after)}}
        return list(self.col.find(query).sort("_id", DESCENDING if newest else ASCENDING).limit(size))


class Lease(Store):
    """Work that must be done once, at a time, by one server: claimed for a while, then free again."""

    COLLECTION = "counters"

    def __init__(self, name: str, seconds: float):
        self.name, self.seconds = f"lease:{name}", seconds

    def take(self) -> bool:
        now = self.now()
        try:
            return self.col.find_one_and_update(
                {"_id": self.name, "expires_at": {"$lte": now}},
                {"$set": {"expires_at": now + timedelta(seconds=self.seconds)}},
                upsert=True, return_document=ReturnDocument.AFTER) is not None
        except DuplicateKeyError:  # held by another: the upsert met its document
            return False
