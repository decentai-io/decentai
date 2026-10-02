"""Secret definitions — the controller. The store lives in
database/stores/data/definitions.py."""

from __future__ import annotations

from database.stores.agents import AgentManifestStore
from database.stores.data.definitions import DefinitionStore
from server.custom_logging import CustomLoggerFactory


class DefinitionController:
    """Secret definitions — one organization's versioned templates, each
    derived from what an installed agent's manifest declares (or from a
    login an agent asked for). Read here, never written: nothing could
    use a shape that no agent declared."""

    def __init__(self):
        # Imported here, not at module scope: secrets.py needs
        # DefinitionStore from this module, so naming SecretStore at the
        # top would close a circle. A definition is the more fundamental
        # of the two — it can be described without a secret existing,
        # while a secret cannot exist without one — so this direction is
        # the one that gives way.
        from database.stores.data.secrets import SecretStore

        self.definitions = DefinitionStore()
        self.secrets = SecretStore()
        self.agents = AgentManifestStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict):
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    def list(self, data: dict, user: dict):
        """Every family, latest version first-class, with the whole version
        chain beside it.

        Neither secrets nor agents follow a family forward — each names the
        exact version it was made against — so "at v4" says nothing about
        where the credentials are. Each version therefore carries its own
        shape and its own tally, which is also what says whether it can be
        deleted: a version nothing names is removable, and only the caller
        looking at that list can tell."""
        org_id = str(user.get("org_id") or "")
        # The readable-name index, reversed: a pin is written against the
        # ref, and a person reads the qualified name.
        names = {
            ref: name
            for name, ref in self.agents.qualified_ids(org_id).items()
        }
        definitions = []
        for doc in self.definitions.list_latest(org_id):
            slug = doc["definition_id"]
            public = DefinitionStore.to_public(doc)

            counts = self.secrets.count_by_version(org_id, slug)
            pins = self.agents.agents_pinning_secret_definition(org_id, slug)

            public["instance_count"] = sum(counts.values())
            public["agent_uses"] = sorted(
                names.get(ref, ref) for ref in pins)
            public["versions"] = [
                {
                    **DefinitionStore.to_public(version),
                    "secret_count": counts.get(version["version"], 0),
                    "agent_ids": sorted(
                        agent_id for agent_id, pinned in pins.items()
                        if pinned == version["version"]
                    ),
                }
                for version in self.definitions.versions(org_id, slug)
            ]
            definitions.append(public)
        return {"definitions": definitions}, 200

    def get(self, data: dict, user: dict):
        """One version: by exact ref, or a family's latest."""
        payload = self._payload(data)
        org_id = str(user.get("org_id") or "")
        ref = str(payload.get("definition_ref") or "")
        if ref:
            doc = self.definitions.get_in(org_id, ref)
        else:
            doc = self.definitions.latest(
                org_id, str(payload.get("definition_id") or ""))
        if doc is None:
            return {"error": "Definition not found."}, 404
        return {"definition": DefinitionStore.to_public(doc)}, 200
