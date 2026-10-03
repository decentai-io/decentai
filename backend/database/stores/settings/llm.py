"""LLM connections — the models an organization's chats may think with.

A connection is its own kind of record, not a secret on a definition:
its shape is the platform's to know (provider, model, endpoint, key), so
making an organization author a definition before it could name a model
was ceremony around a fixed form.

The key is still encrypted exactly the way the secret layer encrypts —
same cipher, same key material, sealed to the document id — and it is
still write-only: nothing here can return it, and only ``use`` (called
by the endpoint the runtime is allowed) decrypts.

Visibility is the shared sharing engine's owner map: a connection is
private until somebody shares it — with groups, with people, or
organization-wide — and every read is filtered by it. The default is a
pointer, not a share: it answers the
chats of whoever can SEE it, and an organization that shares no model
org-wide has simply decided that everyone picks their own — a person
with no visible default chooses a connection in their preferences or
the chat's config.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from contracts.llm_providers import LlmProviders
from database.crypto import SecretCipher
from database.stores.iam import OrgScopedStore
from util import iso, new_id, utc_now


class LlmConnectionStore(OrgScopedStore):
    """An organization's LLM connections, exactly one of them default.

    The default is the organization's answer when nothing narrower
    chose: a person's preference or a chat's own config may name any
    other connection, and does so by id."""

    COLLECTION = "llm_connections"
    ENCRYPTED_FIELDS = ("values",)

    #: How hard a reasoning model thinks before each step; blank is the
    #: provider's default and the only right value for a model that
    #: does not reason.
    EFFORTS = ("", "minimal", "low", "medium", "high")

    @classmethod
    def sharing(cls):
        """The connection's sharing engine, under the INFRASTRUCTURE
        profile every org-wide record type uses.

        The one place this module reaches for governance, and it does so
        lazily: governance sits above the stores in the import graph, so
        a top-level import here would close a cycle through the package
        init."""
        from server.governance import INFRASTRUCTURE, Sharing

        return Sharing(INFRASTRUCTURE, "connection")

    # The owner rules are the shared engine's, named here for this
    # store's callers.
    @classmethod
    def org_wide(cls, owner: Any) -> bool:
        return cls.sharing().org_wide(owner)

    @classmethod
    def clean_owner(cls, owner: Any, creator: str) -> Dict[str, Any]:
        return cls.sharing().clean(owner, creator=creator)

    def _check_owner_exists(self, org_id: str, owner: Dict[str, Any]) -> None:
        self.sharing().check_exists(org_id, owner)

    @classmethod
    def _visibility(cls, user: Dict[str, Any]) -> Dict[str, Any]:
        return cls.sharing().visibility_filter(user)

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """The key never comes back — the same write-only rule the secret
        layer lives by.

        ``resource_ref`` and ``keys`` are deliberately the field names a
        secret's public view uses: every consumer of "which model?" (the
        chat picker, the preference, the block a chat carries) was built
        against that shape, and keeping it means they did not all have to
        change because the storage did."""
        if not doc:
            return None
        owner = doc.get("owner") or {}
        return {
            "resource_ref": doc["_id"],
            "name": doc.get("name", ""),
            "owner": {
                "groups": list(owner.get("groups") or []),
                "users": list(owner.get("users") or []),
            },
            "created_by": doc.get("created_by", ""),
            "keys": {
                "provider": doc.get("provider", ""),
                "model": doc.get("model", ""),
                "endpoint": doc.get("endpoint", ""),
                "reasoning_effort": doc.get("reasoning_effort", ""),
                # What the model is for: a chat thinks with a ``chat``
                # connection; agent routing embeds with an ``embedding``
                # one.
                "purpose": doc.get("purpose", ""),
            },
            "is_default": bool(doc.get("is_default")),
            "created_at": iso(doc.get("created_at")),
            "updated_at": iso(doc.get("updated_at")),
        }

    # ------------------------------------------------------------------

    #: what a connection is for: a chat thinks with a ``chat`` model,
    #: agent routing embeds with an ``embedding`` one, and a spoken
    #: message is written down by a ``transcription`` one
    PURPOSES = ("chat", "embedding", "transcription")

    def _clean(self, fields: Dict[str, Any], partial: bool) -> Dict[str, Any]:
        cleaned: Dict[str, Any] = {}
        if not partial or "purpose" in fields:
            purpose = str(fields.get("purpose") or "chat").strip().lower()
            if purpose not in self.PURPOSES:
                raise ValueError("Purpose must be chat, embedding or transcription.")
            cleaned["purpose"] = purpose
        if not partial or "provider" in fields:
            provider = str(fields.get("provider") or "").strip().lower()
            # The catalog both sides read: a provider accepted here is
            # one the runtime has a connector for, by construction.
            if LlmProviders.find(provider) is None:
                raise ValueError(
                    "That provider is not in the platform's catalog. A "
                    "service that speaks OpenAI's protocol is added as "
                    "openai_compatible, with its address as the endpoint.")
            cleaned["provider"] = provider
        if not partial or "model" in fields:
            model = str(fields.get("model") or "").strip()
            if not model:
                raise ValueError("Model is required — the provider's own "
                                 "name for it, copied exactly.")
            cleaned["model"] = model
        if not partial or "endpoint" in fields:
            endpoint = str(fields.get("endpoint") or "").strip()
            if not endpoint:
                raise ValueError("Endpoint is required.")
            cleaned["endpoint"] = endpoint
        if "reasoning_effort" in fields:
            # How hard a reasoning model thinks before each beat. Blank
            # means the provider's default, and is what a model that does
            # not reason must be left at.
            effort = str(fields.get("reasoning_effort") or "").strip().lower()
            if effort not in self.EFFORTS:
                raise ValueError("Reasoning effort must be blank or one of: "
                                 + ", ".join(e for e in self.EFFORTS if e) + ".")
            cleaned["reasoning_effort"] = effort
        return cleaned

    def _name_taken(self, org_id: str, creator: str, name: str,
                    excluding: str = "") -> bool:
        """Whether THIS person already has a connection by this name.

        Scoped to the creator, not the organization: a connection is
        private until somebody shares it, so two people each keeping
        their own "Anthropic" is the ordinary case. An org-wide check
        would refuse the second one over a record its creator cannot
        see — and say so, which is a leak as well as a refusal."""
        query: Dict[str, Any] = {
            "org_id": org_id, "created_by": creator, "name": name,
        }
        if excluding:
            query["_id"] = {"$ne": excluding}
        return self.col.find_one(query) is not None

    # ------------------------------------------------------------------

    def list(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The connections THIS caller can see, default first."""
        return [
            self.to_public(doc)
            for doc in self.col.find(self._visibility(user))
            .sort([("is_default", -1), ("name", 1)])
        ]

    def visible(self, user: Dict[str, Any],
                doc_id: str) -> Optional[Dict[str, Any]]:
        if not doc_id:
            return None
        return self.col.find_one({
            **self._visibility(user), "_id": str(doc_id)})

    def default(self, org_id: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one({
            "org_id": str(org_id or ""), "is_default": True,
        })

    def create(self, org_id: str, name: Any, fields: Dict[str, Any],
               api_key: Any, created_by: str = "",
               owner: Any = None) -> Dict[str, Any]:
        org_id = str(org_id or "")
        owner = self.clean_owner(owner, str(created_by or ""))
        self._check_owner_exists(org_id, owner)
        name = self._clean_name(name, "A connection name")
        if self._name_taken(org_id, str(created_by or ""), name):
            raise ValueError(f"You already have a connection named '{name}'.")
        key = str(api_key or "")
        if not key.strip():
            raise ValueError("API key is required.")

        doc_id = f"llm_{new_id()}"
        cleaned = self._clean(fields, partial=False)
        doc = {
            "_id": doc_id,
            "org_id": org_id,
            "name": name,
            "owner": owner,
            **cleaned,
            "values": SecretCipher.encrypt({"api_key": key}, doc_id),
            # The first CHAT connection is the default because a default
            # must exist for chats to start; every later one is a choice.
            # An embedding model is never a default: no chat thinks with it.
            "is_default": (self.default(org_id) is None
                           and cleaned["purpose"] == "chat"),
            "created_by": str(created_by or ""),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self._insert_unique(doc, f"You already have a connection named '{name}'.")
        return self.to_public(doc)

    def update(self, org_id: str, doc_id: str, name: Any = None,
               fields: Optional[Dict[str, Any]] = None,
               api_key: Any = None,
               owner: Any = None) -> Optional[Dict[str, Any]]:
        """Partial. A blank api_key keeps the stored one — write-only
        fields can be rotated but never emptied by omission."""
        org_id = str(org_id or "")
        doc = self.get_in(org_id, doc_id)
        if doc is None:
            return None

        changes: Dict[str, Any] = {"updated_at": utc_now()}
        if owner is not None:
            cleaned = self.clean_owner(owner, str(doc.get("created_by") or ""))
            self._check_owner_exists(org_id, cleaned)
            changes["owner"] = cleaned
        if name is not None:
            name = self._clean_name(name, "A connection name")
            if self._name_taken(org_id, str(doc.get("created_by") or ""),
                                name, excluding=doc["_id"]):
                raise ValueError(
                    f"You already have a connection named '{name}'.")
            changes["name"] = name
        if fields:
            changes.update(self._clean(fields, partial=True))
            if (doc.get("is_default")
                    and changes.get("purpose", "chat") != "chat"):
                raise ValueError(
                    "This is the model new chats think with. Make another "
                    "chat model the default before giving it another purpose.")
        if str(api_key or "").strip():
            changes["values"] = SecretCipher.encrypt(
                {"api_key": str(api_key)}, doc["_id"])

        self.col.update_one({"_id": doc["_id"]}, {"$set": changes})
        return self.to_public(self.get_in(org_id, doc_id))

    def set_default(self, org_id: str, doc_id: str) -> Optional[Dict[str, Any]]:
        org_id = str(org_id or "")
        doc = self.get_in(org_id, doc_id)
        if doc is None:
            return None
        self.col.update_many(
            {"org_id": org_id, "is_default": True},
            {"$set": {"is_default": False}})
        self.col.update_one(
            {"_id": doc["_id"]}, {"$set": {"is_default": True}})
        return self.to_public(self.get_in(org_id, doc_id))

    def delete(self, org_id: str, doc_id: str) -> bool:  # type: ignore[override]
        """Deleting the default hands default to the most recently
        touched survivor: an organization with connections but no default
        would fail every new chat for no reason anybody chose."""
        org_id = str(org_id or "")
        doc = self.get_in(org_id, doc_id)
        if doc is None:
            return False
        self.col.delete_one({"_id": doc["_id"]})
        if doc.get("is_default"):
            survivor = self.col.find_one(
                {"org_id": org_id, "purpose": "chat"},
                sort=[("updated_at", -1)])
            if survivor is not None:
                self.col.update_one(
                    {"_id": survivor["_id"]}, {"$set": {"is_default": True}})
        return True

    def use(self, user: Dict[str, Any],
            doc_id: str) -> Optional[Dict[str, Any]]:
        """The whole connection, key included, decrypted in-process.

        The one read that returns the key, for the one consumer that may
        have it: the endpoint serving a delegated runtime — checked
        against the DELEGATING PERSON's visibility, so a chat can only
        think with a model its person may see."""
        doc = self.visible(user, doc_id)
        if doc is None:
            return None
        values = SecretCipher.decrypt(doc.get("values"), doc["_id"])
        return {
            "keys": {
                "provider": doc.get("provider", ""),
                "model": doc.get("model", ""),
                "endpoint": doc.get("endpoint", ""),
                "reasoning_effort": doc.get("reasoning_effort", ""),
            },
            "values": {"api_key": str(values.get("api_key") or "")},
        }

    # ------------------------------------------------------------------
    # Stewardship
    # ------------------------------------------------------------------

    def transfer(self, org_id: str, doc_id: str, to_user_id: str) -> Optional[Dict[str, Any]]:
        doc = self.get_in(str(org_id or ""), str(doc_id or ""))
        if doc is None:
            return None
        self._reassign(doc, str(to_user_id))
        return self.to_public(self.get_in(str(org_id or ""), doc["_id"]))

    def transfer_all(self, org_id: str, from_user_id: str, to_user_id: str) -> int:
        moved = 0
        for doc in self.col.find({"org_id": str(org_id or ""),
                                  "created_by": str(from_user_id or "")}):
            self._reassign(doc, str(to_user_id))
            moved += 1
        return moved

    def count_created_by(self, org_id: str, user_id: str) -> int:
        return self.col.count_documents({"org_id": str(org_id or ""),
                                         "created_by": str(user_id or "")})

    def _reassign(self, doc: Dict[str, Any], to_user_id: str) -> None:
        """The creator changes; the map keeps its groups and people with
        the new steward in; a name the successor already uses gets a
        suffix rather than a collision."""
        owner = dict(doc.get("owner") or {})
        previous = str(doc.get("created_by") or "")
        users = [u for u in (owner.get("users") or []) if u != previous]
        if to_user_id not in users:
            users.append(to_user_id)
        name = str(doc.get("name") or "")
        if self._name_taken(str(doc.get("org_id") or ""), to_user_id, name, excluding=doc["_id"]):
            name = f"{name} (transferred)"
        self.col.update_one({"_id": doc["_id"]}, {"$set": {
            "created_by": to_user_id, "name": name,
            "owner": {"groups": list(owner.get("groups") or []), "users": users},
            "updated_at": utc_now(),
        }})
