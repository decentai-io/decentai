"""The pull door (docs/system/agent-code.md).

A delegated runtime fetches the exact bytes an approval names — digest
and manifest hash from the approval row, never from the request, so a
caller cannot ask for arbitrary content: it names an agent, and the row
decides what bytes that means.
"""

import base64

from api.services.agents.acquisition import AcquisitionError


class AgentPackagesMixin:

    def fetch_package(self, data, user):
        """The runtime's pull door (docs/system/agent-code.md).

        Returns the exact bytes this ORGANIZATION approved for one of its
        own agents, plus everything the installer needs to verify them —
        the digest and the manifest hash come from the approval row, so a
        caller cannot ask for arbitrary content: it names an agent, and
        the row decides what bytes that means. Runtime principals only;
        the delegation carries the organization, which is why a runtime
        can only ever pull code approved by an org whose user is
        actually connected to it."""
        if user.get("principal_type") != "runtime":
            return self._fail(
                data, "forbidden",
                "Only the AI runtime may fetch packages.", 403)

        agent_id = str(self._payload(data).get("agent_id") or "").strip()
        doc = self.store.installed_in(self._org(user), agent_id)
        if doc is None:
            return self._fail(data, "not_found", "Agent not found.", 404)

        digest = str(doc.get("package_digest") or "")
        if not digest:
            return self._fail(
                data, "no_package",
                "This agent has no stored package — install it again.", 409)
        try:
            archive = self.acquisition.approved_package(
                self._org(user), digest)
        except AcquisitionError as exc:
            return self._fail(data, "no_package", str(exc), 404)

        return self._respond(data, {
            "agent_id": agent_id,
            "package_digest": digest,
            "manifest_hash": str(doc.get("manifest_hash") or ""),
            "local_agent_id": str(doc.get("local_agent_id") or ""),
            "package": base64.b64encode(archive).decode("ascii"),
        })

    def prepared(self, data, user):
        """A runtime reports that an agent's code is ready to run on it,
        or would not build — what the Installed page shows as prepared.

        Runtime principals only, and only for an agent of the caller's
        own organization; the digest must be the one the approval pins,
        so a slow report about the last version cannot describe the
        next one — it is answered ``recorded: false`` and changes
        nothing. One runtime's word, deliberately: with several
        processes it means "ready somewhere", which is what a person
        about to open a chat wants to know.

        With it comes what that runtime holds the agent to — a user of
        its own, its files fenced, its connections fenced — kept as it
        was said, so that the agent's page can say which part is not
        enforced where it runs (docs/system/sandbox.md)."""
        if user.get("principal_type") != "runtime":
            return self._fail(
                data, "forbidden",
                "Only the AI runtime may report an agent as prepared.", 403)
        payload = self._payload(data)
        agent_id = str(payload.get("agent_id") or "").strip()
        digest = str(payload.get("package_digest") or "").strip()
        state = str(payload.get("state") or "").strip()
        if state not in ("ready", "failed"):
            return self._fail(
                data, "invalid_request", "state must be ready or failed.")
        doc = self.store.installed_in(self._org(user), agent_id)
        if doc is None:
            return self._fail(data, "not_found", "Agent not found.", 404)
        if not digest or digest != str(doc.get("package_digest") or ""):
            return self._respond(data, {"recorded": False})
        record = self.store.set_prepared(
            agent_id, digest, state, str(payload.get("error") or ""),
            confined=self._confined(payload.get("confined")))
        return self._respond(data, {"recorded": True, "prepared": record})

    #: The parts of what a runtime holds an agent to.
    CONFINED_PARTS = ("user", "files", "network")

    @classmethod
    def _confined(cls, said):
        """What a runtime said it enforces, or None when it said
        nothing a page could repeat: three parts, each true or false."""
        if not isinstance(said, dict) or not all(
                isinstance(said.get(part), bool) for part in cls.CONFINED_PARTS):
            return None
        return {part: said[part] for part in cls.CONFINED_PARTS}

    def pinned_digests(self, data, user):
        """Which packages anybody still points at — for a runtime
        reclaiming its disk.

        Runtime principals only, and the answer is deliberately
        deployment-wide: code on a runtime is named by digest and one
        folder serves every organization that approved those bytes, so
        an answer scoped to the caller's own organization could never
        authorise deleting anything. What travels is a set of hashes —
        no organization, no agent, no name — and the process receiving
        it is the one already holding that code.

        The runtime decides nothing from this alone: it keeps whatever
        is in use, and forgets only what neither this set nor its own
        live work mentions."""
        if user.get("principal_type") != "runtime":
            return self._fail(
                data, "forbidden",
                "Only the AI runtime may read what is pinned.", 403)

        return self._respond(data, {
            "digests": sorted(self.store.pinned_digests_everywhere()),
        })
