"""Agent records — the controller. The store lives in
database/stores/data/records.py, with everything else that touches Mongo.

Two writers, one shape — where a shape exists. An agent writes records
through the delegated runtime, which validates against the manifest on
its own side and sends keys/values directly. A PERSON writing into an
AGENT'S category (``agt_<ref>__<slot>``) sends ``fields`` — a flat dict
the backend validates against what the approved manifest declares:
required fields present, unknown ones refused, each value checked
against its declared type. Raw keys/values from a person are refused
for those categories — an agent will read this data, and the format is
not the writer's to bend.

Records under plain labels stay free-form: they are the person's own
notes under their own name, no agent contract touches them, and the
generic data-layer semantics apply unchanged.

An agent category no installed agent declares any more — uninstalled,
or its update dropped the resource — is read and delete only. Cleaning
up is always possible; authoring into a shape nobody stands behind is
not.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from api.services.data_layer.base import ResourceController
from database.crypto import SecretCipher, SecretCipherError
from database.stores.data.definitions import DefinitionStore
from database.stores.data.records import AgentDataStore


class DataController(ResourceController):

    STORE = AgentDataStore
    ESCAPE_ACTION = "data:record:set_owner_any"

    # ------------------------------------------------------------------
    # The declared shape a category answers to
    # ------------------------------------------------------------------

    @staticmethod
    def _declared(user: Dict[str, Any],
                  category: str) -> Optional[Dict[str, Any]]:
        """The data resource an installed agent declares for this
        category (``agt_<ref>__<slot>``), or None. The prefix names
        exactly one approval — the same property secret grants stand on."""
        from database.stores import AgentManifestStore

        category = str(category or "")
        if "__" not in category or not category.startswith("agt_"):
            return None
        agent_ref, _, slot = category.partition("__")
        agent = AgentManifestStore().installed_in(
            str(user.get("org_id") or ""), agent_ref)
        if agent is None:
            return None
        for resource in (agent.get("manifest") or {}).get(
                "resources", {}).get("data") or []:
            if str(resource.get("id") or "") == slot:
                return resource
        return None

    @staticmethod
    def _spec(resource: Dict[str, Any]) -> Dict[str, Any]:
        """The manifest's field list, normalized to what the definition
        validator expects — label and required filled in, options kept."""
        return {"fields": [
            {
                "name": str(field.get("name") or ""),
                "label": str(field.get("label") or field.get("name") or ""),
                "type": str(field.get("type") or "string"),
                "storage": str(field.get("storage") or "keys"),
                "required": bool(field.get("required")),
                "options": list(field.get("options") or []),
            }
            for field in resource.get("fields") or []
        ]}

    @staticmethod
    def _person_may(resource: Dict[str, Any], operation: str) -> bool:
        """What the manifest's ``user_access`` lets a person do to this
        kind directly. Absent is nothing: the records are the agent's
        to write. Reading and deleting are not the manifest's to grant
        — a person always may (docs/agents/manifest.md)."""
        return operation in (resource.get("user_access") or [])

    REFUSED = ("{agent} keeps these records itself — they can be read and "
               "deleted here, not {doing}.")

    @staticmethod
    def _agent_category(category: Any) -> bool:
        category = str(category or "")
        return category.startswith("agt_") and "__" in category

    @staticmethod
    def _person_sent_raw(payload: Dict[str, Any]) -> bool:
        return "keys" in payload or "values" in payload

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def shapes(self, data: dict, user: dict):
        """Every record type an installed agent declares — the picker's
        contents, fields and all, keyed by the category a created record
        will carry."""
        from database.stores import AgentManifestStore

        store = AgentManifestStore()
        shapes = []
        for doc in store.list(str(user.get("org_id") or "")):
            if doc.get("status") != AgentManifestStore.STATUS_INSTALLED:
                continue
            manifest = doc.get("manifest") or {}
            agent_name = (manifest.get("agent") or {}).get("name") or doc["_id"]
            for resource in (manifest.get("resources") or {}).get(
                    "data") or []:
                shapes.append({
                    "agent_ref": doc["_id"],
                    "agent_name": agent_name,
                    "resource_id": f"{doc['_id']}__{resource.get('id')}",
                    "label": resource.get("label") or resource.get("id"),
                    "description": resource.get("description") or "",
                    "fields": self._spec(resource)["fields"],
                    # What a person may do here besides read and delete:
                    # the picker offers only kinds that say "create".
                    "user_access": list(resource.get("user_access") or []),
                })
        return {"shapes": shapes}, 200

    def create(self, data: dict, user: dict, *, agent_samples: bool = False):
        """``agent_samples`` is the samples door's, in process only: an
        agent's sample sheet writes its own kinds, user_access or not."""
        if user.get("principal_type") == "runtime":
            # The delegated executor validated against the manifest on
            # its side and writes keys/values directly.
            return super().create(data, user)

        payload = self._payload(data)
        category = str(payload.get("resource_id") or "")
        if not self._agent_category(category):
            # A plain label: the person's own free-form record.
            return super().create(data, user)

        if self._person_sent_raw(payload):
            return {"error": "This record type belongs to an agent — "
                             "create it from its declared fields "
                             "(send 'fields')."}, 400
        resource = self._declared(user, category)
        if resource is None:
            return {"error": "No installed agent declares that record "
                             "type."}, 404
        if not agent_samples and not self._person_may(resource, "create"):
            return {"error": self.REFUSED.format(
                agent="The agent", doing="created")}, 403
        try:
            keys, values = DefinitionStore.split_fields(
                self._spec(resource), payload.get("fields"))
        except ValueError as exc:
            return {"error": str(exc)}, 400
        payload["keys"], payload["values"] = keys, values
        return super().create(data, user)

    def update(self, data: dict, user: dict):
        if user.get("principal_type") == "runtime":
            return self._runtime_update(data, user)

        payload = self._payload(data)
        doc = self.store.visible_with_cipher(
            user, str(payload.get("resource_ref") or ""))
        if doc is None:
            return {"error": "Record not found."}, 404
        if not self._agent_category(doc.get("resource_id")):
            return super().update(data, user)

        if self._person_sent_raw(payload):
            return {"error": "This record belongs to an agent — edit it "
                             "through its declared fields "
                             "(send 'fields')."}, 400
        if "fields" not in payload:
            # Sharing alone — the owner path needs no shape.
            return super().update(data, user)

        resource = self._declared(user, str(doc.get("resource_id") or ""))
        if resource is None:
            return {"error": "No installed agent declares this record's "
                             "type any more — it can be read and deleted, "
                             "not edited."}, 409
        if not self._person_may(resource, "update"):
            return {"error": self.REFUSED.format(
                agent="The agent", doing="edited")}, 403
        try:
            keys, values = self._merged(doc, self._spec(resource),
                                        payload.get("fields"))
        except SecretCipherError as exc:
            return self._unreadable(exc)
        except ValueError as exc:
            return {"error": str(exc)}, 400
        payload["keys"] = keys
        if values is not None:
            payload["values"] = values
        return super().update(data, user)

    def _runtime_update(self, data: dict, user: dict):
        """An agent's update is partial at the field level — what its
        simulator does and what the worker protocol's ``fields``
        promises. The keys it sends lay over the stored keys and its
        values over the decrypted blob, exactly as a person's edit
        does; replacing either half wholesale turned "mark it
        confirmed" into "forget its title"."""
        payload = self._payload(data)
        doc = self.store.visible_with_cipher(
            user, str(payload.get("resource_ref") or ""))
        if doc is None:
            return {"error": "Record not found."}, 404
        keys, values = payload.get("keys"), payload.get("values")
        if isinstance(keys, dict):
            payload["keys"] = {**(doc.get("keys") or {}), **keys}
        if isinstance(values, dict):
            try:
                existing = SecretCipher.decrypt(doc.get("values"), doc["_id"])
            except SecretCipherError as exc:
                return self._unreadable(exc)
            payload["values"] = {**existing, **values}
        return super().update(data, user)

    def _unreadable(self, exc: SecretCipherError):
        """Stored values this process cannot decrypt — a key missing
        from SECRET_ENCRYPTION_KEYS, most likely. Writing over them
        would destroy them for good once the key is back, so nothing is
        written."""
        self.logger.error(f"A data record cannot be decrypted: {exc}")
        return {"error": "This record's stored values cannot be read, so it "
                         "cannot be changed: " + str(exc)}, 409

    # ------------------------------------------------------------------
    def _merged(self, doc: Dict[str, Any], spec: Dict[str, Any],
                fields: Any) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """A person's edit, laid over what is stored. Keys merge over
        keys; touched values-fields merge over the decrypted blob before
        the whole is re-encrypted. An undecryptable blob raises
        SecretCipherError: nothing is written over it."""
        keys, values = DefinitionStore.split_fields(
            spec, fields, partial=True)
        merged_keys = {**(doc.get("keys") or {}), **keys}
        if not values:
            return merged_keys, None
        existing = SecretCipher.decrypt(doc.get("values"), doc["_id"])
        return merged_keys, {**existing, **values}
