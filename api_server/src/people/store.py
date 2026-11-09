"""Organizations, people, groups, policies, sessions, API keys and one-time links (api-server.md §2, §4)."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from datetime import timedelta
from typing import Any

import bcrypt
from pymongo import ReturnDocument

from ..storage.database import Database, Store


def hashed(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Passwords:
    """bcrypt, cost 12, over SHA-256, so a long password loses nothing past bcrypt's 72 bytes."""

    MIN, MAX = 10, 256
    ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    _DUMMY = bcrypt.hashpw(b"nobody", bcrypt.gensalt(12))

    @staticmethod
    def _prepared(password: str) -> bytes:
        return base64.b64encode(hashlib.sha256(password.encode()).digest())

    @classmethod
    def hash(cls, password: str) -> str:
        return bcrypt.hashpw(cls._prepared(password), bcrypt.gensalt(12)).decode()

    @classmethod
    def matches(cls, password: str, stored: str | None) -> bool:
        """As slow for an account that doesn't exist as for one that does."""
        return bcrypt.checkpw(cls._prepared(password), (stored or "").encode() or cls._DUMMY) and bool(stored)

    @classmethod
    def check(cls, password: Any) -> str:
        password = str(password or "")
        if not cls.MIN <= len(password) <= cls.MAX:
            raise ValueError(f"A password is {cls.MIN} to {cls.MAX} characters.")
        if password.strip() != password:
            raise ValueError("A password neither starts nor ends with a space.")
        return password

    @classmethod
    def temporary(cls) -> str:
        return "".join(secrets.choice(cls.ALPHABET) for _ in range(16))


class Organizations(Store):
    COLLECTION = "organizations"

    def create(self, name: str) -> dict[str, Any]:
        doc = {"_id": self.new_id(), "name": self.clean_name(name), "status": "active", "settings": {},
               "created_at": self.now()}
        self.col.insert_one(doc)
        return doc

    def active(self, org_id: str) -> bool:
        return self.col.count_documents({"_id": str(org_id or ""), "status": "active"}, limit=1) == 1

    @staticmethod
    def clean_name(name: Any) -> str:
        name = " ".join(str(name or "").split())
        if not 1 <= len(name) <= 100:
            raise ValueError("An organization's name is 1 to 100 characters.")
        return name


class Users(Store):
    COLLECTION = "users"
    INDEXES = (([("email", Store.ASC)], {"unique": True}), ([("org_id", Store.ASC)], {}))
    EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}")

    @classmethod
    def clean_email(cls, email: Any) -> str:
        email = str(email or "").strip().lower()
        if len(email) > 254 or not cls.EMAIL.fullmatch(email):
            raise ValueError("That is not an email address.")
        return email

    @staticmethod
    def clean_name(name: Any) -> str:
        name = " ".join(str(name or "").split())
        if not 1 <= len(name) <= 100:
            raise ValueError("A name is 1 to 100 characters.")
        return name

    def create(self, org_id: str, email: str, name: str, password: str, groups: list[str],
               temporary: bool = False) -> dict[str, Any]:
        doc = {"_id": self.new_id(), "org_id": org_id, "email": self.clean_email(email), "name": self.clean_name(name),
               "password_hash": Passwords.hash(password), "must_change_password": temporary, "groups": groups,
               "status": "active", "stopped": False, "preferences": {}, "created_at": self.now()}
        self.col.insert_one(doc)
        return doc

    def by_email(self, email: str) -> dict[str, Any] | None:
        return self.col.find_one({"email": str(email or "").strip().lower()})

    def in_org(self, org_id: str) -> list[dict[str, Any]]:
        return list(self.col.find({"org_id": org_id}).sort("name", Store.ASC))

    def set(self, user_id: str, **fields: Any) -> dict[str, Any] | None:
        return self.col.find_one_and_update({"_id": user_id}, {"$set": fields}, return_document=ReturnDocument.AFTER)

    @staticmethod
    def public(doc: dict[str, Any]) -> dict[str, Any]:
        return {"user_id": doc["_id"], "email": doc["email"], "name": doc["name"], "groups": doc.get("groups", []),
                "status": doc["status"], "stopped": doc.get("stopped", False),
                "must_change_password": doc.get("must_change_password", False),
                "last_login": doc.get("last_login"), "created_at": doc.get("created_at")}


class Groups(Store):
    """Everyone holds every person without listing them; Administrators is made with the organization."""

    COLLECTION = "groups"
    INDEXES = (([("org_id", Store.ASC), ("name", Store.ASC)], {"unique": True}),)
    EVERYONE, ADMINISTRATORS = "Everyone", "Administrators"

    def create(self, org_id: str, name: str, policies: list[str], everyone: bool = False) -> dict[str, Any]:
        doc = {"_id": self.new_id(), "org_id": org_id, "name": self.clean_name(name), "policies": policies,
               "everyone": everyone, "created_at": self.now()}
        self.col.insert_one(doc)
        return doc

    def everyone(self, org_id: str) -> dict[str, Any] | None:
        return self.col.find_one({"org_id": org_id, "everyone": True})

    def of(self, person: dict[str, Any]) -> list[dict[str, Any]]:
        """The person's groups, Everyone included."""
        return list(self.col.find({"org_id": person["org_id"],
                                   "$or": [{"_id": {"$in": list(person.get("groups") or [])}}, {"everyone": True}]}))

    def in_org(self, org_id: str, ids: list[str] | None = None) -> list[dict[str, Any]]:
        query = {"org_id": org_id, **({"_id": {"$in": [str(i) for i in ids]}} if ids is not None else {})}
        return list(self.col.find(query).sort("name", Store.ASC))

    @staticmethod
    def clean_name(name: Any) -> str:
        name = " ".join(str(name or "").split())
        if not 1 <= len(name) <= 80:
            raise ValueError("A group's name is 1 to 80 characters.")
        return name


class Policies(Store):
    COLLECTION = "policies"
    INDEXES = (([("org_id", Store.ASC), ("name", Store.ASC)], {"unique": True}),)

    def in_org(self, org_id: str, ids: list[str] | None = None) -> list[dict[str, Any]]:
        query = {"org_id": org_id, **({"_id": {"$in": [str(i) for i in ids]}} if ids is not None else {})}
        return list(self.col.find(query).sort("name", Store.ASC))


class Sessions(Store):
    """A session is its token's hash: the token itself is never kept."""

    COLLECTION = "sessions"
    INDEXES = (([("expires_at", Store.ASC)], {"expireAfterSeconds": 0}), ([("user_id", Store.ASC)], {}))
    DAYS, REMEMBERED_DAYS = 7, 90

    def create(self, user: dict[str, Any], remember: bool) -> str:
        token = secrets.token_urlsafe(32)
        days = self.REMEMBERED_DAYS if remember else self.DAYS
        self.col.insert_one({"_id": hashed(token), "user_id": user["_id"], "org_id": user["org_id"],
                             "expires_at": self.expires(days * 86400), "created_at": self.now()})
        return token

    def find(self, token: str) -> dict[str, Any] | None:
        return self.col.find_one({"_id": hashed(token), "expires_at": {"$gt": self.now()}})

    def end(self, token_hash: str) -> None:
        self.col.delete_one({"_id": token_hash})

    def end_all(self, user_id: str) -> None:
        self.col.delete_many({"user_id": user_id})


class ApiKeys(Store):
    COLLECTION = "api_keys"
    INDEXES = (([("hash", Store.ASC)], {"unique": True}), ([("user_id", Store.ASC)], {}))
    PREFIX, MOST, DAYS_MAX = "dk_", 20, 365

    def create(self, user: dict[str, Any], name: Any, days: Any) -> tuple[str, dict[str, Any]]:
        days = int(days) if str(days).isdigit() else 0
        if not 1 <= days <= self.DAYS_MAX:
            raise ValueError(f"A key lasts 1 to {self.DAYS_MAX} days.")
        if self.col.count_documents({"user_id": user["_id"]}) >= self.MOST:
            raise ValueError(f"A person keeps at most {self.MOST} keys.")
        key = self.PREFIX + secrets.token_urlsafe(32)
        doc = {"_id": self.new_id(), "user_id": user["_id"], "org_id": user["org_id"], "hash": hashed(key),
               "name": str(name or "").strip()[:80] or "Key", "shown": key[:8], "expires_at": self.expires(days * 86400),
               "created_at": self.now(), "last_used_at": None}
        self.col.insert_one(doc)
        return key, doc

    def find(self, key: str) -> dict[str, Any] | None:
        return self.col.find_one({"hash": hashed(key), "expires_at": {"$gt": self.now()}})

    def touch(self, doc: dict[str, Any]) -> None:
        """At most once a minute: a key in a loop writes no row a call."""
        now = self.now()
        if not doc.get("last_used_at") or now - doc["last_used_at"] > timedelta(minutes=1):
            self.col.update_one({"_id": doc["_id"]}, {"$set": {"last_used_at": now}})

    @staticmethod
    def public(doc: dict[str, Any]) -> dict[str, Any]:
        return {"key_id": doc["_id"], "name": doc["name"], "shown": doc["shown"], "expires_at": doc["expires_at"],
                "created_at": doc["created_at"], "last_used_at": doc.get("last_used_at")}


class Links(Store):
    """One-time links and states: a random value kept only as its hash, used once, then gone."""

    COLLECTION = "links"
    INDEXES = (([("expires_at", Store.ASC)], {"expireAfterSeconds": 0}),
               ([("kind", Store.ASC), ("email", Store.ASC)], {}))
    SECONDS = {"invitation": 7 * 86400, "signup": 3600, "reset": 3600, "oauth": 600}

    def create(self, kind: str, email: str = "", **data: Any) -> str:
        token = secrets.token_urlsafe(32)
        self.col.insert_one({"_id": hashed(token), "kind": kind, "email": email, **data, "created_at": self.now(),
                             "expires_at": self.expires(self.SECONDS[kind])})
        return token

    def peek(self, kind: str, token: Any) -> dict[str, Any] | None:
        return self.col.find_one({"_id": hashed(str(token or "")), "kind": kind, "expires_at": {"$gt": self.now()}})

    def spend(self, kind: str, token: Any) -> dict[str, Any] | None:
        """The link, once: a second use finds nothing."""
        return self.col.find_one_and_delete({"_id": hashed(str(token or "")), "kind": kind,
                                             "expires_at": {"$gt": self.now()}})

    def recent(self, kind: str, email: str) -> bool:
        """Whether one was sent to this address within the last minute."""
        return self.col.count_documents({"kind": kind, "email": email,
                                         "created_at": {"$gt": self.now() - timedelta(minutes=1)}}, limit=1) > 0


class Deployment(Store):
    """The one document: the operator's organization, and whether first run is done."""

    COLLECTION = Database.DEPLOYMENT
    ID = Database.DEPLOYMENT

    def get(self) -> dict[str, Any]:
        return self.col.find_one({"_id": self.ID}) or {}

    def set(self, **fields: Any) -> None:
        self.col.update_one({"_id": self.ID}, {"$set": fields}, upsert=True)

    def claim_first_run(self, code: str) -> bool:
        """The first visitor with the code, alone."""
        return self.col.find_one_and_update({"_id": self.ID, "first_run_done": {"$ne": True}, "setup_code": code},
                                            {"$set": {"first_run_done": True}}) is not None
