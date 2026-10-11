"""The package door: what a runtime asks as itself, for the whole
deployment (docs/system/agent-code.md).

A runtime keeps one copy of every approved package for every
organization that approved it, so what it needs to know is not any
organization's: which packages are approved anywhere, the bytes of one
by its digest, and where to say that one is ready. It asks with its own
identity (server/authentication/access.py, the runtime service
principal), never as a person and never as a chat. Nothing of who
approved what travels: digests, the hash to verify a manifest against,
a name for the log, and bytes whose name is their own hash.
"""

import base64

from api.endpoints.app.ai.base import AIController
from api.services.agents.acquisition import AgentAcquisition, AcquisitionError
from database.stores import AgentManifestStore
from server.custom_logging import CustomLoggerFactory


class PackageController(AIController):
    def __init__(self):
        self.store = AgentManifestStore()
        self.acquisition = AgentAcquisition()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    def _a_runtime(self, data, user):
        """The refusal for anybody who is not a runtime speaking as
        itself, or None. A person's session and a chat's delegation are
        both refused: the answers here are nobody's in particular."""
        if user.get("principal_type") != "runtime_service":
            return self._fail(
                data, "forbidden",
                "Only the AI runtime, as itself, may use the package door.",
                403)
        return None

    def list(self, data, user):
        """Every package some installed approval points at, anywhere:
        its digest, the manifest hash the installer verifies against,
        and a name for the log. What a runtime stocks itself from, and
        the set it keeps when it reclaims its disk."""
        refused = self._a_runtime(data, user)
        if refused:
            return refused
        return self._respond(data, {"packages": self.store.approved_packages()})

    def fetch(self, data, user):
        """The bytes of one approved package, by its digest. The digest
        is the name and the proof: the installer hashes what arrives
        before writing it, so the door can serve the same bytes to
        every runtime whichever organization stored them."""
        refused = self._a_runtime(data, user)
        if refused:
            return refused
        digest = str(self._payload(data).get("package_digest") or "").strip()
        if not digest:
            return self._fail(data, "invalid_request", "package_digest is required.")
        try:
            archive = self.acquisition.package_anywhere(digest)
        except AcquisitionError as exc:
            return self._fail(data, "not_found", str(exc), 404)
        return self._respond(data, {
            "package_digest": digest,
            "package": base64.b64encode(archive).decode("ascii"),
        })

    #: The parts of what a runtime holds an agent to.
    CONFINED_PARTS = ("user", "files", "network")

    def prepared(self, data, user):
        """A runtime's word that a package is ready to run on it, or
        would not build and why — recorded on every approval, of every
        organization, that pins that digest, which is what the Agents
        page shows as Preparing turning into Ready. A word about a
        digest nobody pins any more changes nothing and is answered
        ``recorded: false``.

        With it comes what that runtime holds the agent to — a user of
        its own, its files fenced, its connections fenced — kept as it
        was said, so an agent's page can say which part is not enforced
        where it runs (docs/system/sandbox.md)."""
        refused = self._a_runtime(data, user)
        if refused:
            return refused
        payload = self._payload(data)
        digest = str(payload.get("package_digest") or "").strip()
        state = str(payload.get("state") or "").strip()
        if not digest:
            return self._fail(data, "invalid_request", "package_digest is required.")
        if state not in ("ready", "failed"):
            return self._fail(
                data, "invalid_request", "state must be ready or failed.")
        recorded = self.store.set_prepared_everywhere(
            digest, state, str(payload.get("error") or ""),
            confined=self._confined(payload.get("confined")))
        return self._respond(data, {"recorded": recorded > 0, "approvals": recorded})

    @classmethod
    def _confined(cls, said):
        """What a runtime said it enforces, or None when it said
        nothing a page could repeat: three parts, each true or false."""
        if not isinstance(said, dict) or not all(
                isinstance(said.get(part), bool) for part in cls.CONFINED_PARTS):
            return None
        return {part: said[part] for part in cls.CONFINED_PARTS}
