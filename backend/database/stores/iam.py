"""IAM stores — one class per entity of the access chain.

    user → groups → roles → policies → permissions

Plus the runtime stores the auth flows lean on: sessions, password resets,
and the login throttle. Other domains have their own files beside this
one.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

from server.authentication.credentials import PasswordHasher
from database.stores.base import MongoStore, access_cache
from util import iso, new_id, utc_now


# ======================================================================
# The organization boundary
# ======================================================================

class OrgScopedStore(MongoStore):
    """A store whose documents belong to one organization.

    Reading by an id the caller supplied is the way one organization's
    administrator reaches another's records, so the two reads that take
    such an id carry the organization beside it. Ids are unguessable;
    this is the belt to that pair of braces.
    """

    def get_in(self, org_id: str, doc_id: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({
            "_id": str(doc_id or ""), "org_id": str(org_id or ""),
        })

    def list_in(self, org_id: str, ids: List[str]) -> List[Dict[str, Any]]:
        if not ids:
            return []
        return list(self.col.find({
            "_id": {"$in": list(ids)}, "org_id": str(org_id or ""),
        }))


# ======================================================================
# Organization — the tenant boundary itself
# ======================================================================

class OrganizationStore(MongoStore):
    """One deployment may hold several; the first is seeded by the launch
    script. Everything else carries its ``org_id``, and every read is
    pinned to the one the caller belongs to."""

    COLLECTION = "organizations"

    STATUS_ACTIVE = "active"
    STATUS_DISABLED = "disabled"

    #: How a chat finds the right agent among many (Settings:Routing).
    #: Below ``threshold`` enabled agents the frame lists them all; past
    #: it the runtime embeds the message, keeps ``candidates`` by
    #: similarity, reranks them with the chat's model when ``rerank``,
    #: and lists ``shortlist``. ``open_max`` agents stay open per chat.
    ROUTING_DEFAULTS = {
        "embedding_connection_id": "",
        "threshold": 15,
        "shortlist": 15,
        "candidates": 50,
        "rerank": True,
        "open_max": 8,
    }
    ROUTING_BOUNDS = {
        "threshold": (1, 1000),
        "shortlist": (1, 100),
        "candidates": (1, 500),
        "open_max": (1, 30),
    }

    #: A spoken message is written down by the organization's
    #: transcription connection (Settings:Speech); none means the
    #: composer offers no microphone.
    SPEECH_DEFAULTS = {"transcription_connection_id": ""}

    #: What agents may do without asking (Settings:Safety). Every
    #: value starts where the platform stood before the setting
    #: existed, so nothing is loosened by an update.
    #:   blocked_sites     names no agent may open; a name covers every
    #:                     host under it
    #:   scripts           a script in a page: a card every time, or
    #:                     once for a site in a chat
    #:   programs          a program: a card every time; not again for a
    #:                     correction that needs nothing new; or only
    #:                     when it reaches a site or uses a credential
    #:   packages          any the card names, or only ``allowed_packages``
    #:   mcp               whether people may add MCP servers and their
    #:                     chats may call them
    SAFETY_DEFAULTS = {
        "blocked_sites": [],
        "scripts": "always",
        "programs": "always",
        "packages": "any",
        "allowed_packages": [],
        "mcp": "allowed",
    }
    SAFETY_CHOICES = {
        "scripts": ("always", "once_per_site"),
        "programs": ("always", "corrections", "quiet"),
        "packages": ("any", "listed"),
        "mcp": ("allowed", "blocked"),
    }
    SAFETY_LIST_MAX = 300
    _SITE = re.compile(
        r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")
    _PACKAGE = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")

    def safety(self, org_id: str) -> Dict[str, Any]:
        """The organization's Safety setting, defaults filled in."""
        doc = self.col.find_one({"_id": str(org_id or "")}) or {}
        stored = doc.get("safety") if isinstance(doc.get("safety"), dict) else {}
        found = {**self.SAFETY_DEFAULTS, **{
            key: stored[key] for key in self.SAFETY_DEFAULTS if key in stored}}
        return {**found, "blocked_sites": list(found["blocked_sites"]),
                "allowed_packages": list(found["allowed_packages"])}

    def set_safety(self, org_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Some of the rows, checked; the rest stand. Raises ValueError
        with the sentence to show."""
        cleaned: Dict[str, Any] = {}
        for key, value in (changes or {}).items():
            if key in self.SAFETY_CHOICES:
                if value not in self.SAFETY_CHOICES[key]:
                    raise ValueError(
                        f"{key} is one of: {', '.join(self.SAFETY_CHOICES[key])}.")
                cleaned[key] = value
            elif key == "blocked_sites":
                cleaned[key] = self._names(
                    value, self._SITE,
                    lambda name: name,
                    "A site is a name, such as example.com: not an address, "
                    "and without https:// or a path. '{name}' is not one.")
            elif key == "allowed_packages":
                cleaned[key] = self._names(
                    value, self._PACKAGE,
                    lambda name: re.sub(r"[-_.]+", "-", name),
                    "A package is named as pip installs it, such as pandas, "
                    "without a version. '{name}' is not one.")
        merged = {**self.safety(org_id), **cleaned}
        self.col.update_one(
            {"_id": str(org_id or "")},
            {"$set": {"safety": merged, "updated_at": utc_now()}})
        return merged

    def _names(self, value: Any, shape, normal, refusal: str) -> List[str]:
        if not isinstance(value, list):
            raise ValueError("A list is expected.")
        if len(value) > self.SAFETY_LIST_MAX:
            raise ValueError(f"A list holds at most {self.SAFETY_LIST_MAX} names.")
        found: List[str] = []
        for item in value:
            name = str(item or "").strip().lower().strip(".")
            if not name:
                continue
            if not shape.match(name):
                raise ValueError(refusal.format(name=str(item)[:80]))
            name = normal(name)
            if name not in found:
                found.append(name)
        return sorted(found)

    def speech(self, org_id: str) -> Dict[str, Any]:
        doc = self.col.find_one({"_id": str(org_id or "")}) or {}
        stored = doc.get("speech") if isinstance(doc.get("speech"), dict) else {}
        return {**self.SPEECH_DEFAULTS, **{
            key: str(stored[key] or "") for key in self.SPEECH_DEFAULTS if key in stored}}

    def set_speech(self, org_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        merged = {**self.speech(org_id), **{
            key: str(value or "").strip() for key, value in (changes or {}).items()
            if key in self.SPEECH_DEFAULTS}}
        self.col.update_one(
            {"_id": str(org_id or "")},
            {"$set": {"speech": merged, "updated_at": utc_now()}})
        return merged

    def routing(self, org_id: str) -> Dict[str, Any]:
        """The organization's routing settings, defaults filled in."""
        doc = self.col.find_one({"_id": str(org_id or "")}) or {}
        stored = doc.get("routing") if isinstance(doc.get("routing"), dict) else {}
        return {**self.ROUTING_DEFAULTS, **{
            key: stored[key] for key in self.ROUTING_DEFAULTS if key in stored}}

    def set_routing(self, org_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Some of the numbers, checked for shape and range; the rest
        stand. Raises ValueError with the sentence to show."""
        cleaned: Dict[str, Any] = {}
        for key, value in (changes or {}).items():
            if key not in self.ROUTING_DEFAULTS:
                continue
            if key == "embedding_connection_id":
                cleaned[key] = str(value or "").strip()
            elif key == "rerank":
                cleaned[key] = bool(value)
            else:
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    raise ValueError(f"{key} must be a whole number.")
                low, high = self.ROUTING_BOUNDS[key]
                if not low <= number <= high:
                    raise ValueError(f"{key} must be between {low} and {high}.")
                cleaned[key] = number
        merged = {**self.routing(org_id), **cleaned}
        if merged["candidates"] < merged["shortlist"]:
            raise ValueError("candidates must be at least the shortlist size.")
        self.col.update_one(
            {"_id": str(org_id or "")},
            {"$set": {"routing": merged, "updated_at": utc_now()}})
        return merged

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        return {
            "org_id": doc["_id"],
            "org_name": doc.get("org_name", ""),
            "status": doc.get("status", OrganizationStore.STATUS_ACTIVE),
            "created_at": iso(doc.get("created_at")),
        }

    def get(self, doc_id: str = "") -> Optional[Dict[str, Any]]:
        """One organization by id. Without an id, the only one there is —
        which is what a single-organization deployment asks for."""
        if doc_id:
            return self.col.find_one({"_id": str(doc_id)})
        return self.col.find_one({})

    def create(self, org_name: str, org_id: str = "") -> Dict[str, Any]:
        """A new organization. No endpoint reaches this: organizations are
        made from the command line (bootstrap/organizations.py)."""
        doc = {
            "_id": org_id or new_id(),
            "org_name": str(org_name or "").strip() or "Organization",
            "status": self.STATUS_ACTIVE,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self.col.insert_one(doc)
        return doc

    def list(self) -> List[Dict[str, Any]]:
        return list(self.col.find().sort("created_at", 1))

    def is_active(self, org_id: str) -> bool:
        """Whether this organization may be used at all.

        An unknown organization is not active, and neither is no
        organization at all: a session naming one that is gone has
        nothing to be a session of, and `get("")` would otherwise hand
        back whichever organization happens to be first. Only a document
        that exists and is not disabled says yes."""
        org_id = str(org_id or "")
        if not org_id:
            return False
        doc = self.col.find_one({"_id": org_id})
        if doc is None:
            return False
        return doc.get("status", self.STATUS_ACTIVE) == self.STATUS_ACTIVE

    def set_status(self, org_id: str, status: str) -> Optional[Dict[str, Any]]:
        """Disabling keeps everything and refuses everyone.

        Nothing is deleted but the sessions: the data stays exactly as it
        was and comes back on enable. Live sessions go now rather than at
        the next request, because a cached one would otherwise outlive
        the decision by the length of its TTL."""
        org_id = str(org_id or "")
        if status not in (self.STATUS_ACTIVE, self.STATUS_DISABLED):
            raise ValueError("status must be 'active' or 'disabled'.")

        self.col.update_one(
            {"_id": org_id},
            {"$set": {"status": status, "updated_at": utc_now()}},
        )
        if status == self.STATUS_DISABLED:
            # Locally: the AI stores import this module, not the reverse.
            from database.stores.delegations import RuntimeSessionStore

            sessions, runtime = SessionStore(), RuntimeSessionStore()
            for user in UserStore().list(org_id):
                sessions.delete_for_user(user["_id"])
                runtime.delete_for_user(user["_id"])
        return self.get(org_id)

    def ensure(self, org_name: str, org_id: str = "") -> Dict[str, Any]:
        """Seed the organization if it does not exist yet. Idempotent."""
        existing = self.get()
        if existing is not None:
            return existing
        return self.create(org_name, org_id)

    def rename(self, org_id: str, org_name: str) -> Optional[Dict[str, Any]]:
        org_name = self._clean_name(org_name, "An organization name")
        self.col.update_one(
            {"_id": str(org_id or "")},
            {"$set": {"org_name": org_name, "updated_at": utc_now()}},
        )
        return self.get(org_id)


# ======================================================================
# User — the smallest identity; access is exactly assigned_groups
# ======================================================================

class UserStore(OrgScopedStore):
    """Email is globally unique and is the login identity: one person
    belongs to one organization, so an address resolves to a user and a
    user carries its organization. No direct role assignment, no
    ownership tier. Passwords are stored only as bcrypt hashes and never
    leave this class."""

    COLLECTION = "users"

    STATUS_ACTIVE = "active"
    STATUS_DISABLED = "disabled"

    @staticmethod
    def normalize_email(email: str) -> str:
        return str(email or "").strip().lower()

    def peers_of(self, org_id: str, group_ids: list,
                 excluding: str) -> list:
        """Everyone in this organization sharing one of these explicit
        groups — the reach of "share with a person", sorted by name."""
        if not group_ids:
            return []
        return list(self.col.find({
            "org_id": str(org_id or ""),
            "assigned_groups": {"$in": list(group_ids)},
            "_id": {"$ne": str(excluding or "")},
        }).sort([("user_name", 1)]))

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """UI-safe view — never includes the password hash."""
        if not doc:
            return None
        return {
            "user_id": doc["_id"],
            "org_id": doc.get("org_id"),
            "email": doc.get("email", ""),
            "user_name": doc.get("user_name", ""),
            "status": doc.get("status", UserStore.STATUS_ACTIVE),
            "assigned_groups": list(doc.get("assigned_groups") or []),
            "created_at": iso(doc.get("created_at")),
            "last_login_at": iso(doc.get("last_login_at")),
        }

    # ------------------------------------------------------------------
    def create(
        self,
        org_id: str,
        email: str,
        user_name: str,
        password_hash: Optional[str] = None,
        assigned_groups: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        email = self.normalize_email(email)
        if not email or "@" not in email or len(email) > 254:
            raise ValueError("A valid email is required.")
        if len(str(user_name or "")) > 120:
            raise ValueError("A name must be 120 characters or fewer.")

        doc = {
            "_id": new_id(),
            "org_id": org_id,
            "email": email,
            "user_name": str(user_name or "").strip(),
            "status": self.STATUS_ACTIVE,
            "password_hash": password_hash,
            "assigned_groups": list(assigned_groups or []),
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "last_login_at": None,
        }
        return self._insert_unique(doc, "A user with this email already exists.")

    # ------------------------------------------------------------------
    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({"email": self.normalize_email(email)})

    def list(self, org_id: str) -> List[Dict[str, Any]]:
        return list(
            self.col.find({"org_id": str(org_id or "")}).sort("created_at", 1)
        )

    def list_by_group(self, group_id: str) -> List[Dict[str, Any]]:
        """The members of a group — membership lives on the user."""
        return list(self.col.find({"assigned_groups": group_id}))

    # ------------------------------------------------------------------
    def update_profile(self, user_id: str, user_name: str) -> Optional[Dict[str, Any]]:
        access_cache.drop_user(user_id)
        self.col.update_one(
            {"_id": user_id},
            {"$set": {"user_name": str(user_name or "").strip(),
                      "updated_at": utc_now()}},
        )
        return self.get(user_id)

    def set_stopped(self, user_id: str, stopped: bool) -> None:
        """The person's own switch (api/services/chat_session/halt.py):
        while it is set, nothing of theirs is dialed or runs."""
        change = ({"$set": {"stopped_at": utc_now()}} if stopped
                  else {"$unset": {"stopped_at": ""}})
        self.col.update_one({"_id": user_id}, change)

    def stopped(self, user_id: str) -> bool:
        return bool(user_id) and self.col.count_documents(
            {"_id": user_id, "stopped_at": {"$exists": True}}, limit=1) == 1

    def set_chat_preferences(
        self, user_id: str, values: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """What this person wants new chats to start with.

        One writer for every chat preference, because there is one reader:
        the settings package decides what a preference means and this
        stores whatever it hands over. A setter per field meant a new
        setting was a new method here, and the vocabulary lived in two
        places.

        An empty value CLEARS the field rather than storing emptiness —
        the model preference in particular is either a secret's ref or
        nothing at all, and removing the key keeps older user documents
        needing no migration. An empty LIST is a real answer, though
        ("no agents"), so only the model's kind of empty unsets.
        """
        assign: Dict[str, Any] = {"updated_at": utc_now()}
        remove: Dict[str, Any] = {}

        for key, value in (values or {}).items():
            field = f"preferences.chat.{key}"
            if isinstance(value, list):
                assign[field] = list(value)
            elif value or value == 0:
                assign[field] = value
            else:
                remove[field] = ""

        update: Dict[str, Any] = {"$set": assign}
        if remove:
            update["$unset"] = remove
        self.col.update_one({"_id": user_id}, update)
        return self.get(user_id)

    def set_notification_preferences(
        self, user_id: str, values: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """How this person wants to be reached when not looking — today
        one choice: whether an email may follow when no device can be
        pushed to."""
        assign: Dict[str, Any] = {"updated_at": utc_now()}
        for key, value in (values or {}).items():
            if key in ("email",):
                assign[f"preferences.notifications.{key}"] = bool(value)
        self.col.update_one({"_id": user_id}, {"$set": assign})
        return self.get(user_id)

    def set_secret_default(
        self, user_id: str, family: str, resource_ref: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Which credential answers when several could.

        Per person, per family, naming a record and never a value. Chats
        that bound one explicitly are unaffected — this is the answer for
        every chat that did not choose."""
        key = f"preferences.secrets.defaults.{str(family)}"
        if resource_ref:
            update: Dict[str, Any] = {"$set": {
                key: str(resource_ref), "updated_at": utc_now(),
            }}
        else:
            update = {"$unset": {key: ""}, "$set": {"updated_at": utc_now()}}
        self.col.update_one({"_id": user_id}, update)
        return self.get(user_id)

    def secret_default(self, user_id: str, family: str) -> str:
        doc = self.get(user_id) or {}
        defaults = ((doc.get("preferences") or {}).get("secrets") or {}
                    ).get("defaults") or {}
        return str(defaults.get(str(family)) or "")

    def set_password(self, user_id: str, password_hash: str) -> None:
        access_cache.drop_user(user_id)
        self.col.update_one(
            {"_id": user_id},
            {"$set": {"password_hash": password_hash, "updated_at": utc_now()}},
        )

    def set_status(self, user_id: str, status: str) -> None:
        access_cache.drop_user(user_id)
        self.col.update_one(
            {"_id": user_id}, {"$set": {"status": status, "updated_at": utc_now()}}
        )

    def set_groups(self, user_id: str, group_ids: List[str]) -> Optional[Dict[str, Any]]:
        access_cache.drop_user(user_id)
        self.col.update_one(
            {"_id": user_id},
            {"$set": {"assigned_groups": list(group_ids or []),
                      "updated_at": utc_now()}},
        )
        return self.get(user_id)

    def remove_group_everywhere(self, group_id: str) -> int:
        """Detach a deleted group from every user."""
        access_cache.bump_permissions_version()
        return self.col.update_many(
            {"assigned_groups": group_id},
            {"$pull": {"assigned_groups": group_id}},
        ).modified_count

    def touch_login(self, user_id: str) -> None:
        self.col.update_one({"_id": user_id}, {"$set": {"last_login_at": utc_now()}})

    def delete(self, user_id: str) -> bool:
        access_cache.drop_user(user_id)
        return super().delete(user_id)


# ======================================================================
# Group — a named bundle of roles
# ======================================================================

class GroupStore(OrgScopedStore):
    """Membership lives on the USER; a group only carries roles. Each
    organization has a built-in Everyone group with implicit universal
    membership — one document per organization, recognised by its id so
    it can be neither renamed nor deleted — otherwise ordinary."""

    COLLECTION = "groups"

    # The value stored in an owner map to mean "everybody here". It names
    # no document: a read already pins org_id, so the same sentinel means
    # a different set of people in each organization — which is what lets
    # everything already shared stay correct.
    EVERYONE_ID = "everyone"

    @classmethod
    def everyone_id(cls, org_id: str) -> str:
        """The DOCUMENT holding this organization's Everyone roles."""
        return f"{cls.EVERYONE_ID}_{str(org_id or '').strip()}"

    @classmethod
    def resolve(cls, org_id: str, group_id: str) -> str:
        """The DOCUMENT id for a stored group reference.

        ``everyone`` is a sentinel meaning "this organization's Everyone",
        not an id — it is what shared documents carry, and it resolves
        against the organization the reader already belongs to.
        """
        group_id = str(group_id or "")
        return cls.everyone_id(org_id) if group_id == cls.EVERYONE_ID else group_id

    @classmethod
    def is_everyone(cls, group_id: str) -> bool:
        group_id = str(group_id or "")
        return group_id == cls.EVERYONE_ID or group_id.startswith(
            f"{cls.EVERYONE_ID}_"
        )

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        return {
            "group_id": doc["_id"],
            "org_id": doc.get("org_id"),
            "group_name": doc.get("group_name", ""),
            "assigned_roles": list(doc.get("assigned_roles") or []),
            "is_builtin": GroupStore.is_everyone(doc["_id"]),
            "created_at": iso(doc.get("created_at")),
        }

    def create(
        self,
        org_id: str,
        group_name: str,
        assigned_roles: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        doc = {
            "_id": new_id(),
            "org_id": org_id,
            "group_name": self._clean_name(group_name, "A group name"),
            "assigned_roles": list(assigned_roles or []),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        return self._insert_unique(doc, "A group with this name already exists.")

    def ensure_everyone(self, org_id: str) -> Dict[str, Any]:
        """Seed the built-in all-users group if absent (launch script).
        Idempotent — an existing Everyone is returned untouched, so admin
        edits to its roles survive re-runs."""
        existing = self.get(self.everyone_id(org_id))
        if existing is not None:
            return existing
        doc = {
            "_id": self.everyone_id(org_id),
            "org_id": org_id,
            "group_name": "Everyone",
            "assigned_roles": [],
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self.col.insert_one(doc)
        return doc

    def get_by_name(self, org_id: str, group_name: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({
            "org_id": str(org_id or ""),
            "group_name": str(group_name or "").strip(),
        })

    def list(self, org_id: str) -> List[Dict[str, Any]]:
        return list(
            self.col.find({"org_id": str(org_id or "")}).sort("group_name", 1)
        )

    def names_for(
        self, org_id: str, group_ids: List[str],
    ) -> List[Dict[str, str]]:
        """The ids a user carries, as `{group_id, group_name}` to read.

        Membership is stored as ids because that is what policy resolves;
        a screen showing them is showing its working rather than an
        answer. One read of the organization's groups answers for any
        number of users, so a list does not become a query per row.

        An id with no group behind it is kept, named as missing: a
        membership pointing at something deleted is worth seeing, not
        worth hiding.
        """
        wanted = [str(gid) for gid in (group_ids or [])]
        if not wanted:
            return []

        known = {
            group["_id"]: group.get("group_name", "")
            for group in self.list(org_id)
        }
        return [
            {"group_id": gid, "group_name": known.get(gid) or "(deleted group)"}
            for gid in wanted
        ]

    def rename(self, group_id: str, group_name: str) -> Optional[Dict[str, Any]]:
        if self.is_everyone(group_id):
            raise ValueError("The Everyone group cannot be renamed.")
        self._update_unique(
            {"_id": group_id},
            {"group_name": self._clean_name(group_name, "A group name"),
             "updated_at": utc_now()},
            "A group with this name already exists.",
        )
        return self.get(group_id)

    def set_roles(self, group_id: str, role_ids: List[str]) -> Optional[Dict[str, Any]]:
        """Replace the roles every member of this group inherits."""
        access_cache.bump_permissions_version()
        self.col.update_one(
            {"_id": group_id},
            {"$set": {"assigned_roles": list(role_ids or []),
                      "updated_at": utc_now()}},
        )
        return self.get(group_id)

    def remove_role_everywhere(self, role_id: str) -> int:
        """Detach a deleted role from every group."""
        access_cache.bump_permissions_version()
        return self.col.update_many(
            {"assigned_roles": role_id},
            {"$pull": {"assigned_roles": role_id}},
        ).modified_count

    def delete(self, group_id: str) -> bool:
        if self.is_everyone(group_id):
            raise ValueError("The Everyone group cannot be deleted.")
        access_cache.bump_permissions_version()
        return super().delete(group_id)

    def restore(self, doc: Dict[str, Any]) -> None:
        access_cache.bump_permissions_version()
        super().restore(doc)


# ======================================================================
# Role — a named bundle of policies
# ======================================================================

class RoleStore(OrgScopedStore):
    """A role carries no statements of its own: ``assigned_policies``
    references PolicyStore documents, and the union of their statements is
    what the role grants."""

    COLLECTION = "roles"

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        return {
            "role_id": doc["_id"],
            "role_name": doc.get("role_name", ""),
            "description": doc.get("description", ""),
            "assigned_policies": list(doc.get("assigned_policies") or []),
            "created_at": iso(doc.get("created_at")),
        }

    def create(
        self,
        org_id: str,
        role_name: str,
        assigned_policies: Optional[List[str]] = None,
        description: str = "",
    ) -> Dict[str, Any]:
        doc = {
            "_id": new_id(),
            "org_id": str(org_id or ""),
            "role_name": self._clean_name(role_name, "A role name"),
            "description": str(description or "").strip(),
            "assigned_policies": list(assigned_policies or []),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        return self._insert_unique(doc, "A role with this name already exists.")

    def get_by_name(self, org_id: str, role_name: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({
            "org_id": str(org_id or ""),
            "role_name": str(role_name or "").strip(),
        })

    def list(self, org_id: str) -> List[Dict[str, Any]]:
        return list(
            self.col.find({"org_id": str(org_id or "")}).sort("role_name", 1)
        )

    def update(
        self,
        role_id: str,
        role_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        changes: Dict[str, Any] = {"updated_at": utc_now()}
        if role_name is not None:
            changes["role_name"] = self._clean_name(role_name, "A role name")
        if description is not None:
            changes["description"] = str(description).strip()

        self._update_unique(
            {"_id": role_id}, changes, "A role with this name already exists."
        )
        return self.get(role_id)

    def set_policies(self, role_id: str, policy_ids: List[str]) -> Optional[Dict[str, Any]]:
        access_cache.bump_permissions_version()
        self.col.update_one(
            {"_id": role_id},
            {"$set": {"assigned_policies": list(policy_ids or []),
                      "updated_at": utc_now()}},
        )
        return self.get(role_id)

    def remove_policy_everywhere(self, policy_id: str) -> int:
        """Detach a deleted policy from every role."""
        access_cache.bump_permissions_version()
        return self.col.update_many(
            {"assigned_policies": policy_id},
            {"$pull": {"assigned_policies": policy_id}},
        ).modified_count

    def delete(self, role_id: str) -> bool:
        access_cache.bump_permissions_version()
        return super().delete(role_id)

    def restore(self, doc: Dict[str, Any]) -> None:
        access_cache.bump_permissions_version()
        super().restore(doc)


# ======================================================================
# Policy — a named permissions document, the leaf of the chain
# ======================================================================

class PolicyStore(OrgScopedStore):
    """``permissions`` is a JSON document of AWS-style statements. Every
    action string is validated against the ActionCatalog at save time, so a
    typo cannot be stored as policy that silently never matches."""

    COLLECTION = "policies"

    EFFECT_ALLOW = "Allow"
    EFFECT_DENY = "Deny"
    VALID_EFFECTS = (EFFECT_ALLOW, EFFECT_DENY)

    # Size caps: permissions are walked on every authorization question, so
    # a pathological document must be rejected at the door, not evaluated.
    MAX_STATEMENTS = 50
    MAX_ACTIONS_PER_STATEMENT = 100

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        return {
            "policy_id": doc["_id"],
            "name": doc.get("name", ""),
            "description": doc.get("description", ""),
            "permissions": doc.get("permissions", {"statements": []}),
            "created_at": iso(doc.get("created_at")),
        }

    @classmethod
    def validate_permissions(cls, permissions: Any) -> Dict[str, Any]:
        """Normalize + validate a permissions document. Raises ValueError
        with a human-readable reason."""
        from server.authentication.policy import ActionCatalog

        if not isinstance(permissions, dict):
            raise ValueError("Permissions must be an object with a 'statements' list.")

        statements = permissions.get("statements")
        if not isinstance(statements, list) or not statements:
            raise ValueError("Permissions need at least one statement.")
        if len(statements) > cls.MAX_STATEMENTS:
            raise ValueError(f"At most {cls.MAX_STATEMENTS} statements per policy.")

        normalized: List[Dict[str, Any]] = []
        for index, statement in enumerate(statements, start=1):
            if not isinstance(statement, dict):
                raise ValueError(f"Statement {index} must be an object.")

            effect = str(statement.get("effect") or "").capitalize()
            if effect not in cls.VALID_EFFECTS:
                raise ValueError(f"Statement {index}: effect must be 'Allow' or 'Deny'.")

            raw_actions = statement.get("actions")
            if isinstance(raw_actions, str):
                raw_actions = [raw_actions]
            if not isinstance(raw_actions, list) or not raw_actions:
                raise ValueError(f"Statement {index}: at least one action is required.")
            if len(raw_actions) > cls.MAX_ACTIONS_PER_STATEMENT:
                raise ValueError(
                    f"Statement {index}: at most "
                    f"{cls.MAX_ACTIONS_PER_STATEMENT} actions per statement."
                )

            actions: List[str] = []
            for action in raw_actions:
                action = str(action).strip()
                if not ActionCatalog.is_valid_pattern(action):
                    raise ValueError(f"Statement {index}: unknown action '{action}'.")
                actions.append(action)

            raw_resources = statement.get("resources", ["*"])
            if isinstance(raw_resources, str):
                raw_resources = [raw_resources]
            resources = [str(r).strip() for r in raw_resources or ["*"]]

            # Resource scoping is not a wired dimension yet: every permission
            # check asks with resource "*", so a statement scoped to anything
            # narrower would silently never apply. Reject it rather than store
            # policy that looks effective but is not.
            if any(r != "*" for r in resources):
                raise ValueError(
                    f"Statement {index}: resource scoping is not supported yet — "
                    f"use \"*\"."
                )

            normalized_statement = {
                "effect": effect,
                "actions": actions,
                "resources": resources,
            }
            constraints = statement.get("constraints")
            if constraints is not None:
                if not isinstance(constraints, dict):
                    raise ValueError(
                        f"Statement {index}: constraints must be an object."
                    )
                normalized_statement["constraints"] = constraints
            normalized.append(normalized_statement)

        return {"statements": normalized}

    # ------------------------------------------------------------------
    def create(
        self, org_id: str, name: str, permissions: Dict[str, Any],
        description: str = "",
    ) -> Dict[str, Any]:
        doc = {
            "_id": new_id(),
            "org_id": str(org_id or ""),
            "name": self._clean_name(name, "A policy name"),
            "description": str(description or "").strip(),
            "permissions": self.validate_permissions(permissions),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        return self._insert_unique(doc, "A policy with this name already exists.")

    def get_by_name(self, org_id: str, name: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({
            "org_id": str(org_id or ""), "name": str(name or "").strip(),
        })

    def list(self, org_id: str) -> List[Dict[str, Any]]:
        return list(self.col.find({"org_id": str(org_id or "")}).sort("name", 1))

    def update(
        self,
        policy_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        permissions: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        access_cache.bump_permissions_version()
        changes: Dict[str, Any] = {"updated_at": utc_now()}
        if name is not None:
            changes["name"] = self._clean_name(name, "A policy name")
        if description is not None:
            changes["description"] = str(description).strip()
        if permissions is not None:
            changes["permissions"] = self.validate_permissions(permissions)

        self._update_unique(
            {"_id": policy_id}, changes, "A policy with this name already exists."
        )
        return self.get(policy_id)

    def delete(self, policy_id: str) -> bool:
        access_cache.bump_permissions_version()
        return super().delete(policy_id)

    def restore(self, doc: Dict[str, Any]) -> None:
        access_cache.bump_permissions_version()
        super().restore(doc)


# ======================================================================
# Invitation — how users join; there is no signup
# ======================================================================

class InvitationStore(OrgScopedStore):
    """A promise of membership: an email, the groups waiting for it, and a
    secret proving the holder was invited. Only the HASH of the token is
    stored; one live invitation per email (re-inviting rotates the token,
    which quietly kills the previous link)."""

    COLLECTION = "invitations"

    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"

    LIFETIME = timedelta(days=7)

    @property
    def lifetime_days(self) -> int:
        return self.LIFETIME.days

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """UI-safe view — never includes the token hash."""
        if not doc:
            return None
        return {
            "invitation_id": doc["_id"],
            "email": doc.get("email", ""),
            "status": doc.get("status", InvitationStore.STATUS_PENDING),
            "assigned_groups": list(doc.get("assigned_groups") or []),
            "invited_by_email": doc.get("invited_by_email", ""),
            "created_at": iso(doc.get("created_at")),
            "expires_at": iso(doc.get("expires_at")),
            "accepted_at": iso(doc.get("accepted_at")),
        }

    # ------------------------------------------------------------------
    def issue(
        self,
        org_id: str,
        email: str,
        invited_by_email: str,
        assigned_groups: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create or refresh the invitation for this address.

        Returns the document with a transient ``token`` key holding the raw
        secret — never stored and never returned again. An address has
        one invitation at a time; one another organization sent, still
        waiting, is refused (ValueError) rather than moved here.
        """
        email = str(email or "").strip().lower()
        token = PasswordHasher.random_secret()
        now = utc_now()

        waiting = self.col.find_one({
            "email": email, "status": self.STATUS_PENDING,
            "org_id": {"$ne": str(org_id or "")},
            "expires_at": {"$gt": now},
        })
        if waiting is not None:
            raise ValueError(
                "This address is already invited to another organization.")

        fields = {
            "org_id": str(org_id or ""),
            "email": email,
            "token_hash": PasswordHasher.token_fingerprint(token),
            "status": self.STATUS_PENDING,
            "assigned_groups": list(assigned_groups or []),
            "invited_by_email": invited_by_email,
            "created_at": now,
            "expires_at": now + self.LIFETIME,
            "accepted_at": None,
        }

        self.col.update_one(
            {"email": email},
            {"$set": fields, "$setOnInsert": {"_id": new_id()}},
            upsert=True,
        )

        doc = self.col.find_one({"email": email})
        return {**doc, "token": token}

    def get_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Resolve a raw token to a LIVE invitation. Unknown, accepted and
        expired tokens are indistinguishable from the outside."""
        if not token:
            return None

        doc = self.col.find_one(
            {"token_hash": PasswordHasher.token_fingerprint(token)}
        )
        if doc is None or doc.get("status") != self.STATUS_PENDING:
            return None

        expires_at = doc.get("expires_at")
        if expires_at is not None and expires_at <= utc_now():
            return None

        return doc

    def list_pending(self, org_id: str) -> List[Dict[str, Any]]:
        """Live invitations only — expired ones are not offers any more."""
        return list(
            self.col.find({
                "org_id": str(org_id or ""),
                "status": self.STATUS_PENDING,
                "expires_at": {"$gt": utc_now()},
            }).sort("created_at", -1)
        )

    def mark_accepted(self, invitation_id: str) -> None:
        """Spend the invitation: the token hash goes with it, so the link
        cannot be replayed to create a second account."""
        self.col.update_one(
            {"_id": invitation_id},
            {
                "$set": {
                    "status": self.STATUS_ACCEPTED,
                    "accepted_at": utc_now(),
                },
                "$unset": {"token_hash": ""},
            },
        )

    def revoke(self, invitation_id: str) -> bool:
        """Withdraw an unaccepted invitation — nothing was created yet."""
        return self.col.delete_one({
            "_id": invitation_id,
            "status": self.STATUS_PENDING,
        }).deleted_count > 0

    def delete_for_email(self, email: str) -> int:
        return self.col.delete_many(
            {"email": str(email or "").strip().lower()}
        ).deleted_count


# ======================================================================
# Session — server-side, so logout and revocation are real
# ======================================================================

class SessionStore(MongoStore):
    """The cookie's JWT only NAMES a session; the row here is what makes it
    valid. Logout deletes the row; disabling a user deletes all of theirs;
    a Mongo TTL index sweeps expiries."""

    COLLECTION = "sessions"

    # A week, matching the cookie's lifetime.
    LIFETIME = timedelta(days=7)

    def create(
        self,
        user_id: str,
        org_id: str,
        user_agent: str = "",
        ip_address: str = "",
    ) -> Dict[str, Any]:
        now = utc_now()
        doc = {
            "_id": new_id(),
            "user_id": user_id,
            "org_id": org_id,
            "user_agent": str(user_agent or "")[:400],
            "ip_address": str(ip_address or "")[:64],
            "created_at": now,
            "expires_at": now + self.LIFETIME,
        }
        self.col.insert_one(doc)
        return doc

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        """A live session, or None. Expiry is checked here rather than
        trusted to the TTL monitor, which only sweeps about once a minute."""
        if not session_id:
            return None
        doc = self.col.find_one({"_id": session_id})
        if doc is None:
            return None

        expires_at = doc.get("expires_at")
        if expires_at is not None and expires_at <= utc_now():
            self.delete(session_id)
            return None
        return doc

    def delete(self, session_id: str) -> bool:
        access_cache.drop_session(session_id)
        return super().delete(session_id)

    def delete_for_user(self, user_id: str) -> int:
        """Every session of one user — on disable, delete, password change."""
        access_cache.drop_user(user_id)
        return self.col.delete_many({"user_id": user_id}).deleted_count


# ======================================================================
# PasswordReset — a short-lived, single-use proof of mailbox control
# ======================================================================

class PasswordResetStore(MongoStore):
    """Mirrors invitations (hash-only storage, uniform failures) with two
    deliberate differences: an HOUR not a week, and one live link per user —
    issuing a new one drops the previous."""

    COLLECTION = "password_resets"

    LIFETIME = timedelta(hours=1)
    # How soon another mail may be sent for the same account. Stops the form
    # being used to bombard someone's inbox.
    RESEND_INTERVAL = timedelta(minutes=1)

    @property
    def lifetime_minutes(self) -> int:
        return int(self.LIFETIME.total_seconds() // 60)

    def issue(self, user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a reset for this user, or None if one was just sent."""
        existing = self.col.find_one({"user_id": user["_id"]})
        if existing is not None:
            issued_at = existing.get("created_at")
            if issued_at and issued_at > utc_now() - self.RESEND_INTERVAL:
                return None

        token = PasswordHasher.random_secret()
        now = utc_now()

        doc = {
            "_id": new_id(),
            "user_id": user["_id"],
            "org_id": user.get("org_id"),
            "email": user.get("email", ""),
            "token_hash": PasswordHasher.token_fingerprint(token),
            "created_at": now,
            "expires_at": now + self.LIFETIME,
        }

        # One live link per user: replace rather than accumulate.
        self.col.delete_many({"user_id": user["_id"]})
        self.col.insert_one(doc)

        return {**doc, "token": token}

    def get_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Resolve a raw token to a live reset, or None."""
        if not token:
            return None

        doc = self.col.find_one(
            {"token_hash": PasswordHasher.token_fingerprint(token)}
        )
        if doc is None:
            return None

        expires_at = doc.get("expires_at")
        if expires_at is not None and expires_at <= utc_now():
            self.col.delete_one({"_id": doc["_id"]})
            return None

        return doc

    def spend(self, reset_id: str) -> None:
        """Consume the link. The row goes entirely."""
        self.col.delete_one({"_id": reset_id})

    def delete_for_user(self, user_id: str) -> int:
        """Drop outstanding links — after any password change, so a reset
        mail already in flight cannot undo it."""
        return self.col.delete_many({"user_id": user_id}).deleted_count


# ======================================================================
# LoginThrottle — failed attempts cost time, so guessing does not scale
# ======================================================================

class LoginThrottle(MongoStore):
    """Two counters, both self-expiring (TTL on ``expires_at``): a handful
    of wrong passwords locks that account briefly; a higher per-IP cap
    catches one address hammering many accounts. Success clears the account
    counter but NOT the IP counter — success against one account must not
    refill an attacker's budget against the rest."""

    COLLECTION = "auth_throttle"

    WINDOW = timedelta(minutes=15)
    MAX_PER_ACCOUNT = 5
    MAX_PER_IP = 20

    def _count(self, key: str) -> int:
        doc = self.col.find_one({"_id": key})
        if doc is None or doc.get("expires_at", utc_now()) <= utc_now():
            return 0
        return int(doc.get("attempts") or 0)

    def _bump(self, key: str) -> None:
        now = utc_now()
        doc = self.col.find_one({"_id": key})
        if doc is None or doc.get("expires_at", now) <= now:
            # New window. Overwrite whatever expired row may linger.
            self.col.update_one(
                {"_id": key},
                {"$set": {"attempts": 1, "expires_at": now + self.WINDOW}},
                upsert=True,
            )
        else:
            self.col.update_one({"_id": key}, {"$inc": {"attempts": 1}})

    # ------------------------------------------------------------------
    def is_locked(self, email: str, ip_address: str = "") -> bool:
        if email and self._count(f"acct:{email}") >= self.MAX_PER_ACCOUNT:
            return True
        if ip_address and self._count(f"ip:{ip_address}") >= self.MAX_PER_IP:
            return True
        return False

    def record_failure(self, email: str, ip_address: str = "") -> None:
        if email:
            self._bump(f"acct:{email}")
        if ip_address:
            self._bump(f"ip:{ip_address}")

    def record_success(self, email: str) -> None:
        if email:
            self.col.delete_one({"_id": f"acct:{email}"})
