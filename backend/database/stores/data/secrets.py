"""Credentials — the strictest domain in the data layer.

Values are never read back through the API: the only way out is
``use``, and only for a delegated runtime acting inside a chat. Create
and update are definition-driven, so what a caller may store is decided
by the version its secret was created under, not by what they send.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from database.stores.data.definitions import DefinitionStore
from database.stores.data.resources import ResourceStore
from database.crypto import SecretCipher, SecretCipherError
from util import new_id, utc_now


class SecretStore(ResourceStore):
    """Credentials — database logins, provider keys.

    Every secret is created FROM a definition version and validated against
    it, here in the store so no writer (controller or in-process) can skip
    it. ``name`` is the built-in instance label, unique per
    (definition, creator).
    """

    COLLECTION = "secrets"
    TYPE = "secret"
    LABEL = "secret"

    def _name_taken(self, definition_id: str, created_by: str, name: str, excluding: Optional[str] = None) -> bool:
        query: Dict[str, Any] = {
            "type": self.TYPE,
            "resource_id": definition_id,
            "created_by": created_by,
            "name": name,
        }
        if excluding:
            query["_id"] = {"$ne": excluding}
        return self.col.find_one(query) is not None

    def by_account(self, org_id: str, created_by: str, definition_id: str,
                   account: str) -> Optional[Dict[str, Any]]:
        """This person's credential for that account, if they have one.

        ``account`` is what the provider calls whoever signed in, kept as
        a plain key rather than inside the encrypted values precisely so
        it can be asked about. Matched on the definition FAMILY
        (``resource_id``), not the version: republishing a definition
        must not make the same account look like a new one.

        Scoped to one person on purpose. Two colleagues may each hold
        their own credential for one shared mailbox, and neither should
        be able to overwrite the other's by signing in."""
        if not account:
            return None
        return self.col.find_one({
            "type": self.TYPE,
            "org_id": str(org_id or ""),
            "created_by": str(created_by or ""),
            "resource_id": str(definition_id or ""),
            "keys.account": account,
        })

    # Definition-driven create/update replace the generic resource ones.

    def create(  # type: ignore[override]
        self, user: Dict[str, Any], definition: Dict[str, Any], name: Any, owner: Dict[str, Any], fields: Any = None,
    ) -> Dict[str, Any]:

        name = self._clean_name(name, "A secret name")
        user_id = str(user.get("user_id") or "")
        if self._name_taken(definition["definition_id"], user_id, name):
            raise ValueError(
                f"You already have a {definition.get('label', 'secret')} "
                f"named '{name}'."
            )

        keys, values = DefinitionStore.split_fields(definition, fields)
        keys = self.clean_keys(keys)

        doc_id = new_id()
        doc = {
            "_id": doc_id,
            "type": self.TYPE,
            "org_id": str(user.get("org_id") or ""),
            "name": name,
            "resource_id": definition["definition_id"],
            "definition_ref": definition["_id"],
            "definition_version": definition["version"],
            "owner": self.clean_owner(owner),
            "keys": keys,
            "values": SecretCipher.encrypt(self.clean_values(values), doc_id),
            "created_by": user_id,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self._insert_unique(
            doc,
            f"You already have a {definition.get('label', 'secret')} "
            f"named '{name}'.",
        )
        return self.to_public(doc)

    def update(  # type: ignore[override]
        self, doc: Dict[str, Any], definition: Dict[str, Any], name: Any = None, owner: Optional[Dict[str, Any]] = None,
            fields: Any = None) -> Optional[Dict[str, Any]]:
        """Partial update, validated against the instance's RECORDED
        version. Values are merged decrypt-side (in process), so rotating
        one field keeps the others."""

        doc_id = doc["_id"]
        changes: Dict[str, Any] = {"updated_at": utc_now()}

        if name is not None:
            name = self._clean_name(name, "A secret name")
            if self._name_taken(
                doc["resource_id"], doc.get("created_by", ""), name,
                excluding=doc_id,
            ):
                raise ValueError(f"You already have a secret named '{name}'.")
            changes["name"] = name

        if owner is not None:
            changes["owner"] = self.clean_owner(owner)

        if fields:
            keys_part, values_part = DefinitionStore.split_fields(
                definition, fields, partial=True
            )
            if keys_part:
                changes["keys"] = self.clean_keys(
                    {**(doc.get("keys") or {}), **keys_part}
                )
            if values_part:
                # Merge with the stored plaintext — re-read the full doc,
                # since callers hold the values-stripped view.
                stored = super().get(doc_id) or {}
                current = SecretCipher.decrypt(stored.get("values"), doc_id)
                changes["values"] = SecretCipher.encrypt(
                    self.clean_values({**current, **values_part}), doc_id
                )

        self.col.update_one({"_id": doc_id}, {"$set": changes})
        return self.to_public(self.get(doc_id))

    def migrate(self, doc: Dict[str, Any], target: Dict[str, Any],
                fields: Any = None) -> Optional[Dict[str, Any]]:
        """Move an instance onto a newer version of its family.

        The stored fields carry over where the new shape still knows
        them — decrypted in-process, so an unchanged password is not
        retyped — fields the new shape dropped go, and the merged whole
        is validated in FULL against the target, so a newly required
        field must arrive with the request. This is the one write that
        changes which version a secret answers to, and it only ever
        moves forward to what the agent now expects."""

        doc_id = doc["_id"]
        stored = super().get(doc_id) or {}
        current = {
            **(doc.get("keys") or {}),
            **SecretCipher.decrypt(stored.get("values"), doc_id),
        }
        known = {f["name"] for f in target.get("fields") or []}
        merged = {k: v for k, v in current.items() if k in known}
        for name, value in (fields or {}).items():
            merged[name] = value

        keys, values = DefinitionStore.split_fields(target, merged)
        self.col.update_one({"_id": doc_id}, {"$set": {
            "keys": self.clean_keys(keys),
            "values": SecretCipher.encrypt(self.clean_values(values), doc_id),
            "definition_ref": target["_id"],
            "definition_version": target["version"],
            "updated_at": utc_now(),
        }})
        return self.to_public(self.get(doc_id))

    def move_forward(self, org_id: str, target: Dict[str, Any]) -> Tuple[int, int]:
        """Every instance of a family still on an older version, moved
        onto ``target`` where that needs nothing from anybody: the
        fields the new shape still knows carry over, the ones it
        dropped go, and nothing it requires is missing. An instance the
        new shape asks more of stays where it is, for its owner to
        complete — as does a connected account whose sign-in changed,
        which only signing in again can bring up to date.

        Returns (moved, left). The agent's update is what calls this:
        without it every update strands the credentials saved before
        it on a shape the agent no longer reads."""
        slug = str(target.get("definition_id") or "")
        definitions = DefinitionStore()
        moved = left = 0
        for doc in self.col.find({
                "type": self.TYPE, "org_id": str(org_id or ""),
                "resource_id": slug,
                "definition_ref": {"$ne": target["_id"]}}):
            before = definitions.get_in(
                org_id, str(doc.get("definition_ref") or "")) or {}
            try:
                if (before.get("oauth") or None) != (target.get("oauth") or None):
                    raise ValueError("the sign-in changed")
                self.migrate(doc, target)
                moved += 1
            except (ValueError, SecretCipherError) as exc:
                left += 1
                self.logger.info(
                    f"Secret {doc['_id']} stays on its version of {slug}: {exc}")
        return moved, left

    def count_for_definition(self, org_id: str, definition_id: str) -> int:
        """Instances across ALL versions of a family — what blocks deletion.

        This organization's own: a count is small, but it is still a fact
        about somebody else's deployment when it is not."""
        return self.col.count_documents({
            "type": self.TYPE, "org_id": str(org_id or ""),
            "resource_id": str(definition_id or ""),
        })

    def count_by_version(self, org_id: str, definition_id: str) -> Dict[int, int]:
        """The same instances, split by the version each was created under.
        A family is published forward while its secrets stay where they were
        made, so "3 in use" and "now at v4" can both be true and describe
        entirely different versions."""
        counts: Dict[int, int] = {}
        for doc in self.col.find(
            {"type": self.TYPE, "org_id": str(org_id or ""),
             "resource_id": str(definition_id or "")},
            {"definition_version": 1},
        ):
            version = int(doc.get("definition_version") or 1)
            counts[version] = counts.get(version, 0) + 1
        return counts
