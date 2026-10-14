"""Store foundations: MongoStore (shared plumbing) and AccessCache.

The cache lives at the bottom of the import graph because the stores are
what invalidate it — single-process, so dropping entries on mutation is
exactly equivalent to re-reading Mongo. A short TTL backstops out-of-band
writes.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from pymongo.errors import DuplicateKeyError

from server.setup.app_state import get_db


class AccessCache:
    TTL_SECONDS = 60

    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._statements: Dict[str, Dict[str, Any]] = {}
        self._permissions_version = 0
        # Moves on every invalidation. A resolution carries the
        # generation it began reading at, and is not kept when an
        # invalidation came in the meantime: what it read may be from
        # before a write that has since landed.
        self._generation = 0

    def generation(self) -> int:
        """Taken before reading the database for a resolution, and
        handed back when the result is put."""
        return self._generation

    def _current(self, since: Optional[int]) -> bool:
        return since is None or since == self._generation

    # ── sessions (session_id → user dict) ──────────────────────────────

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        entry = self._sessions.get(session_id)
        if entry is None:
            return None
        if time.monotonic() - entry["cached_at"] > self.TTL_SECONDS:
            del self._sessions[session_id]
            return None
        # A copy: callers annotate the dict (the ws route does), and that
        # must never leak into the cached original.
        return dict(entry["user"])

    def put_session(self, session_id: str, user: Dict[str, Any],
                    since: Optional[int] = None) -> None:
        if not self._current(since):
            return
        self._sessions[session_id] = {
            "user": dict(user),
            "cached_at": time.monotonic(),
        }

    def drop_session(self, session_id: str) -> None:
        self._generation += 1
        self._sessions.pop(session_id, None)

    def drop_user(self, user_id: str) -> None:
        """Everything cached about one user — sessions and statements.
        Called when they are disabled, deleted, re-grouped, or change
        password."""
        self._generation += 1
        self._statements.pop(user_id, None)
        for session_id in [
            sid for sid, entry in self._sessions.items()
            if entry["user"].get("user_id") == user_id
        ]:
            del self._sessions[session_id]

    # ── statements (user_id → resolved policy statements) ──────────────

    def get_statements(self, user_id: str) -> Optional[List[Dict[str, Any]]]:
        entry = self._statements.get(user_id)
        if entry is None:
            return None
        if (
            entry["version"] != self._permissions_version
            or time.monotonic() - entry["cached_at"] > self.TTL_SECONDS
        ):
            del self._statements[user_id]
            return None
        return entry["statements"]

    def put_statements(self, user_id: str, statements: List[Dict[str, Any]],
                       since: Optional[int] = None) -> None:
        if not self._current(since):
            return
        self._statements[user_id] = {
            "statements": statements,
            "version": self._permissions_version,
            "cached_at": time.monotonic(),
        }

    def bump_permissions_version(self) -> None:
        """Any group/role/policy mutation: every cached resolution is stale."""
        self._generation += 1
        self._permissions_version += 1

    def clear(self) -> None:
        self._generation += 1
        self._sessions.clear()
        self._statements.clear()
        self._permissions_version += 1

    # ── a write, with what it changes invalidated on both sides ────────
    #
    # Before, so nothing cached is served while the write runs; after,
    # so a resolution that read the old rows in between and was put
    # before the write landed is gone too, and one still reading is
    # refused its put by the generation it carries.

    @contextmanager
    def changing_user(self, user_id: str) -> Iterator[None]:
        self.drop_user(user_id)
        try:
            yield
        finally:
            self.drop_user(user_id)

    @contextmanager
    def changing_session(self, session_id: str) -> Iterator[None]:
        self.drop_session(session_id)
        try:
            yield
        finally:
            self.drop_session(session_id)

    @contextmanager
    def changing_permissions(self) -> Iterator[None]:
        self.bump_permissions_version()
        try:
            yield
        finally:
            self.bump_permissions_version()


access_cache = AccessCache()


class MongoStore:
    COLLECTION: str = ""

    #: The fields of this collection that hold a blob encrypted with
    #: the secret key (database/crypto.py), each under its document's
    #: id. Declared here so re-encrypting onto a new key
    #: (bootstrap/reencrypt_secrets.py) finds every one of them.
    ENCRYPTED_FIELDS: tuple = ()

    def __init__(self):
        self.db = get_db()

    @property
    def col(self):
        return self.db.collection(self.COLLECTION)

    # ------------------------------------------------------------------
    @staticmethod
    def _clean_name(value: Any, label: str) -> str:
        """The name validation every entity applies: stripped, required,
        bounded. Raises ValueError with the message the UI shows verbatim."""
        value = str(value or "").strip()
        if not value:
            raise ValueError(f"{label} is required.")
        if len(value) > 120:
            raise ValueError(f"{label} must be 120 characters or fewer.")
        return value

    def _insert_unique(self, doc: Dict[str, Any], taken: str) -> Dict[str, Any]:
        """Insert against a unique index; a collision becomes the human
        message rather than a driver exception."""
        try:
            self.col.insert_one(doc)
        except DuplicateKeyError as exc:
            raise ValueError(taken) from exc
        return doc

    def _update_unique(self, filter_: Dict[str, Any], changes: Dict[str, Any],
                       taken: str) -> None:
        try:
            self.col.update_one(filter_, {"$set": changes})
        except DuplicateKeyError as exc:
            raise ValueError(taken) from exc

    # ------------------------------------------------------------------
    def get(self, doc_id: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({"_id": doc_id})

    def list_by_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        if not ids:
            return []
        return list(self.col.find({"_id": {"$in": list(ids)}}))

    def delete(self, doc_id: str) -> bool:
        return self.col.delete_one({"_id": doc_id}).deleted_count > 0

    def restore(self, doc: Dict[str, Any]) -> None:
        """Reinsert a just-deleted document — the undo half of the lockout
        check (delete → verify someone can still manage access → restore)."""
        self.col.insert_one(doc)


