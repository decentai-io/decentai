"""The agents endpoint controller — assembled from its concerns.

One class still, because the router dispatches agents:agent:<action> to
one registered controller and every public method on it IS an endpoint.
What changed is that a person can now find an endpoint by its concern:

    sources_controller.py   the saved places code comes from
    catalog_controller.py   what is on offer, and what installing means
    install_controller.py   the approval lifecycle: install and delete
    packages_controller.py  the pull door a delegated runtime fetches by
    grants_controller.py    who may ask an agent to do things
    secret_grants_controller.py
                            which saved credential an agent may use
    samples_controller.py   the sample data an agent ships, loaded per person

The mixins share this class's stores and helpers; nothing here decides
anything an install means — fetching and keeping code is
api/services/agents/, and the bytes live in database/agent_packages.
"""


from api.services.agents.acquisition import AgentAcquisition
from database.stores.data.definitions import DefinitionStore
from database.stores import (
    AgentGrantStore, AgentManifestStore, AgentSecretGrantStore,
    AgentSourceStore,
)
from server.authentication.policy import PolicyEngine
from server.custom_logging import CustomLoggerFactory

from api.endpoints.app.ai.base import AIController

from api.endpoints.app.agents.catalog_controller import AgentCatalogMixin
from api.endpoints.app.agents.grants_controller import AgentGrantsMixin
from api.endpoints.app.agents.install_controller import AgentInstallMixin
from api.endpoints.app.agents.packages_controller import AgentPackagesMixin
from api.endpoints.app.agents.samples_controller import AgentSamplesMixin
from api.endpoints.app.agents.secret_grants_controller import (
    AgentSecretGrantsMixin,
)
from api.endpoints.app.agents.sources_controller import AgentSourcesMixin


class AgentController(AgentSourcesMixin, AgentCatalogMixin, AgentInstallMixin,
                      AgentPackagesMixin, AgentGrantsMixin,
                      AgentSecretGrantsMixin, AgentSamplesMixin, AIController):
    def __init__(self):
        self.store = AgentManifestStore()
        self.source_store = AgentSourceStore()
        self.grant_store = AgentGrantStore()
        self.secret_grant_store = AgentSecretGrantStore()
        self.acquisition = AgentAcquisition()
        self.definitions = DefinitionStore()
        # For the escape grants: an administrator managing infrastructure
        # a colleague set up.
        self.policy = PolicyEngine()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _inline_credential(payload):
        """The credential a request carries for a place not saved yet. A
        saved source's credential comes from its own row instead."""
        cred = payload.get("credential")
        if not isinstance(cred, dict):
            return None
        token = str(cred.get("token") or "").strip()
        if not token:
            return None
        return {"username": str(cred.get("username") or "").strip(),
                "token": token}

    def list(self, data, user):
        return self._respond(data, {
            "agents": [self._summary(doc)
                       for doc in self.store.list(self._org(user))],
        })

    def get(self, data, user):
        doc = self.store.get_in(
            self._org(user), str(self._payload(data).get("agent_id") or ""))
        if doc is None:
            return self._fail(data, "not_found", "Agent is not installed.", 404)
        return self._respond(data, {
            "agent": self._summary(doc), "manifest": doc["manifest"],
        })

    @staticmethod
    def _readiness(doc):
        """What a runtime last said about THIS version's code — None
        until one has built it."""
        record = doc.get("prepared") or {}
        if not record or record.get("digest") != str(doc.get("package_digest") or ""):
            return None
        readiness = {"state": record.get("state") or "",
                     "error": record.get("error") or ""}
        # What the runtime that spoke holds the agent to.
        if isinstance(record.get("confined"), dict):
            readiness["confined"] = record["confined"]
        return readiness

    @staticmethod
    def _summary(doc):
        manifest = doc.get("manifest") or {}
        return {
            "agent_id": doc["_id"],
            # What a person reads. Access is granted against the ref
            # above, so this is a label rather than an identity.
            "qualified_id": doc.get("qualified_id") or doc["_id"],
            "local_agent_id": (doc.get("local_agent_id")
                               or (manifest.get("agent") or {}).get("id", "")),
            "version": doc.get("version"),
            "status": doc.get("status"),
            "name": (manifest.get("agent") or {}).get("name", ""),
            "description": (manifest.get("agent") or {}).get("description", ""),
            "functions": [
                f"{doc['_id']}.{tool['id']}.{function['id']}"
                for tool in manifest.get("tools") or []
                for function in tool.get("functions") or []
            ],
            "resources": doc.get("resources") or {},
            # The vocabulary a grant for this agent can be narrowed by.
            "scopes": sorted(
                ((manifest.get("authorization") or {}).get("scopes") or {}).keys()
            ),
            "prepared": AgentController._readiness(doc),
            "source": doc.get("source") or {},
            # What code this actually is. The repository and commit above
            # say where it came from; this says what was kept, and is the
            # only one of the three that cannot change upstream.
            "package_digest": doc.get("package_digest", ""),
        }
