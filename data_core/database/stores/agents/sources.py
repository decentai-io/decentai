"""Saved agent sources — the roster of places code comes from.

A source is a repository an organization saved; its catalog is a
snapshot of what that repository offered when last read. The credential
that reads a private repository lives ON the source, encrypted, and
never returns. Visibility follows the sharing engine: who inside the
organization may see it, and nobody outside it."""

from __future__ import annotations

from typing import Any, Dict, Optional

from database.stores.base import MongoStore
from util import new_id, utc_now


class AgentSourceStore(MongoStore):
    """Saved agent catalogs — where agents come from.

    A source is a repository an organization saved, with the token
    that reads it if it is private, encrypted on the row.

    A source belongs to the organization that saved it and reaches
    nobody outside it. Two organizations wanting the same repository
    save it twice: rows are cheap, and neither one then holds the
    other's supply of code — no listing for one tenant to withdraw, no
    catalog for one tenant to re-point under another's agents. Who may
    see it INSIDE the organization is the owner map, exactly as for
    connections and secrets.
    """

    COLLECTION = "ai_agent_sources"
    ENCRYPTED_FIELDS = ("credential_token",)

    KIND_GIT = "git"

    @classmethod
    def sharing(cls):
        """A source's sharing engine, under the INFRASTRUCTURE profile.

        The one place this module reaches for governance, and it does so
        lazily: governance sits above the stores in the import graph, so
        a top-level import here would close a cycle through the package
        init."""
        from server.governance import INFRASTRUCTURE, Sharing

        return Sharing(INFRASTRUCTURE, "source")

    def list_for(self, user: Dict[str, Any], escape: bool = False) -> list:
        """The sources THIS caller may see — their organization's own.

        Within the organization the owner map filters, exactly as it
        does for connections and secrets. With ``escape`` — the
        manage-any grant — every source of the organization is listed,
        since one cannot manage what one cannot see."""
        query = {"org_id": str(user.get("org_id") or "")}
        if not escape:
            query.update(self.sharing().visibility_or(user))
        return list(self.col.find(query).sort("created_at", 1))

    def create(self, user: Dict[str, Any], *, name: str, url: str, ref: str,
               credential: Optional[Dict[str, str]] = None,
               owner: Any = None,
               kind: str = KIND_GIT) -> Dict[str, Any]:
        """A source, owned by the caller's organization and private to it.

        The credential lives ON the source — the repository and the token
        that reads it are one fact about one place. The token is
        encrypted at rest exactly the way the secret layer encrypts, and
        nothing returns it."""
        from database.crypto import SecretCipher

        now = utc_now()
        org_id = str(user.get("org_id") or "")
        creator_id = str(user.get("user_id") or "")
        sharing = self.sharing()
        owner = sharing.clean(owner, creator=creator_id)
        sharing.check_exists(org_id, owner)

        doc_id = f"src_{new_id()}"
        token = str((credential or {}).get("token") or "").strip()
        doc = {"_id": doc_id,
               "org_id": org_id,
               "name": name, "url": url, "ref": ref,
               "kind": str(kind or self.KIND_GIT),
               # Who inside the organization may see it — the same owner
               # map connections and secrets use.
               "owner": owner,
               "created_by_id": creator_id,

               "credential_user": str(
                   (credential or {}).get("username") or "").strip(),
               "credential_token": (SecretCipher.encrypt(
                   {"token": token}, doc_id) if token else None),
               "status": "new",
               "catalog": None, "last_resolved_sha": "", "last_error": "",
               "created_by": str(user.get("email") or ""),
               "created_at": now, "updated_at": now}
        self.col.insert_one(doc)
        return doc

    @staticmethod
    def has_credential(doc: Dict[str, Any]) -> bool:
        return bool((doc or {}).get("credential_token"))

    def credential_of(self, doc: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """The stored credential, decrypted in-process — None for a
        public repository. Never travels through a response."""
        if not self.has_credential(doc):
            return None
        from database.crypto import SecretCipher

        values = SecretCipher.decrypt(doc.get("credential_token"), doc["_id"])
        return {"username": str(doc.get("credential_user") or ""),
                "token": str(values.get("token") or "")}

    def set_credential(self, source_id: str, username: Any, token: Any) -> None:
        """Username always; the token only when one was typed — blank
        keeps the stored token, so editing the username never demands
        the token back."""
        from database.crypto import SecretCipher

        changes: Dict[str, Any] = {
            "credential_user": str(username or "").strip(),
            "updated_at": utc_now(),
        }
        if str(token or "").strip():
            changes["credential_token"] = SecretCipher.encrypt(
                {"token": str(token)}, str(source_id))
        self.col.update_one({"_id": str(source_id)}, {"$set": changes})

    def clear_credential(self, source_id: str) -> None:
        self.col.update_one({"_id": str(source_id)}, {"$set": {
            "credential_user": "", "credential_token": None,
            "updated_at": utc_now()}})

    def find_or_create(self, user: Dict[str, Any], *, url: str, ref: str,
                       credential: Optional[Dict[str, str]] = None,
                       name: str = "") -> Dict[str, Any]:
        """The saved source for this repository, saving it if it is new.

        Installing straight from a URL still came from somewhere, and an
        agent is recognised again by (organization, source, local id) —
        so an install with no source has no way back to itself, and
        installing it twice would approve it twice under two identities.
        Naming the place is what makes the second install a repair."""
        existing = self.col.find_one({
            "org_id": str(user.get("org_id") or ""), "url": url, "ref": ref,
        })
        if existing is not None:
            return existing
        return self.create(
            user, name=name or url.rstrip("/").rsplit("/", 1)[-1] or url,
            url=url, ref=ref, credential=credential,
        )

    # `get` and `delete` come from MongoStore — a source with no reader
    # in the question is only touched by paths that already resolved
    # their authorization through `get_for` or `owned_by`.

    def edit(self, source_id: str, changes: Dict[str, Any]) -> None:
        """Apply field changes and stamp updated_at. DuplicateKeyError
        travels up — pointing a source at an already-saved repository is
        the caller's sentence to write."""
        self.col.update_one(
            {"_id": str(source_id)},
            {"$set": {**changes, "updated_at": utc_now()}},
        )

    def record_catalog(self, source_id: str, catalog: Dict[str, Any],
                       sha: str) -> None:
        """A successful read: the snapshot, the commit it came from, and
        a clean bill of health."""
        now = utc_now()
        self.col.update_one({"_id": str(source_id)}, {"$set": {
            "catalog": catalog, "last_resolved_sha": str(sha or ""),
            "status": "ready", "last_error": "", "last_checked_at": now,
            "updated_at": now,
        }})

    def record_failure(self, source_id: str, error: str) -> None:
        """A failed read, kept where the page shows it — last_error is
        the field that exists to explain exactly this."""
        self.col.update_one({"_id": str(source_id)}, {"$set": {
            "status": "error", "last_error": str(error or ""),
            "last_checked_at": utc_now(),
        }})

    def get_for(self, user: Dict[str, Any], source_id: str):
        """A source this user may READ — one of their own organization's,
        shown to them.

        Reading is not changing: `owned_by` is what editing and deleting
        go through, so a colleague's source may be installed from without
        being altered. Refreshing reads through here: it rewrites the
        catalog snapshot with what the repository offers, the same
        whoever asks."""
        return self.col.find_one({
            "_id": source_id,
            "org_id": str(user.get("org_id") or ""),
            **self.sharing().visibility_or(user),
        })

    def owned_by(self, user: Dict[str, Any], source_id: str,
                 escape: bool = False):
        """A source this user may CHANGE — one THEY created, or any of
        the organization's when ``escape`` says they hold the
        manage-any grant.

        The connections rule, kept on purpose: being shown a source —
        even org-wide — is permission to install from it, never
        authority over it. The escape is the administrator's, so an
        organization is never locked out of infrastructure a colleague
        set up."""
        doc = self.col.find_one({
            "_id": source_id,
            "org_id": str(user.get("org_id") or ""),
        })
        if doc is None:
            return None
        if escape:
            return doc
        creator = str(doc.get("created_by_id") or "")
        return doc if creator and creator == str(user.get("user_id") or "") else None

    def set_creator(self, source_id: str, user_id: str) -> None:
        """Hand stewardship to another member: the creator changes, the
        visibility map keeps everyone it named with the new steward in."""
        doc = self.col.find_one({"_id": str(source_id)}) or {}
        owner = doc.get("owner")
        if isinstance(owner, dict):
            users = [u for u in owner.get("users") or []
                     if u != str(doc.get("created_by_id") or "")]
            if user_id not in users:
                users.append(user_id)
            owner = {"groups": list(owner.get("groups") or []), "users": users}
        update = {"created_by_id": str(user_id), "updated_at": utc_now()}
        if owner is not None:
            update["owner"] = owner
        self.col.update_one({"_id": str(source_id)}, {"$set": update})

    def set_owner(self, source_id: str, owner: Dict[str, Any]) -> None:
        self.col.update_one({"_id": str(source_id)}, {"$set": {
            "owner": owner, "updated_at": utc_now()}})

    def transfer_all(self, org_id: str, from_user_id: str, to_user_id: str) -> int:
        moved = 0
        for doc in self.col.find({"org_id": str(org_id or ""),
                                  "created_by_id": str(from_user_id or "")}):
            self.set_creator(doc["_id"], str(to_user_id))
            moved += 1
        return moved

    def count_created_by(self, org_id: str, user_id: str) -> int:
        return self.col.count_documents({"org_id": str(org_id or ""),
                                         "created_by_id": str(user_id or "")})
