"""Who may ask an agent to do things — the grants an administrator edits."""


from database.stores import AgentGrantStore, AuditStore


class AgentGrantsMixin:

    def _grant_on_install(self, user, agent_ref: str) -> None:
        """A newly installed agent reaches its installer, and no further.

        Installing something nobody can use reads as broken, so the
        platform makes the smallest grant that is obviously right: the
        groups the installer is in, plus the installer by name so an
        administrator who belongs to no group is not locked out of what
        they just approved. Everything wider is somebody's decision.

        Only on a FIRST install. Re-approving a version is a repair, and
        rebuilding the grant would quietly undo whatever access was
        arranged since."""
        if self.grant_store.for_agent(self._org(user), agent_ref):
            return
        groups = list(user.get("assigned_groups") or [])
        try:
            self.grant_store.create(
                self._org(user), agent_ref,
                {"groups": groups,
                 "users": [str(user.get("user_id") or "")]},
                created_by=str(user.get("email") or ""),
            )
        except ValueError as exc:  # pragma: no cover - defensive
            self.logger.warning(
                f"Could not grant {agent_ref} to its installer: {exc}")

    def grants(self, data, user):
        """Who may use one installed agent."""
        agent_id = str(self._payload(data).get("agent_id") or "")
        if self.store.get_in(self._org(user), agent_id) is None:
            return self._fail(data, "not_found", "Agent is not installed.", 404)
        return self._respond(data, {
            "grants": [AgentGrantStore.to_public(doc)
                       for doc in self.grant_store.for_agent(self._org(user), agent_id)],
        })

    def grant(self, data, user):
        payload = self._payload(data)
        agent_id = str(payload.get("agent_id") or "")
        if self.store.get_in(self._org(user), agent_id) is None:
            return self._fail(data, "not_found", "Agent is not installed.", 404)
        try:
            doc = self.grant_store.create(
                self._org(user), agent_id,
                payload.get("owner"),
                payload.get("functions", AgentGrantStore.ALL_FUNCTIONS),
                payload.get("constraints"),
                created_by=str(user.get("email") or ""),
            )
        except ValueError as exc:
            return self._fail(data, "invalid_request", str(exc))

        AuditStore().append(
            "agent.granted", user, resource_refs=[agent_id],
            details={"grant_id": doc["_id"], "owner": doc["owner"],
                     "functions": doc["functions"]},
        )
        return self._respond(data, {"grant": AgentGrantStore.to_public(doc)})

    def revoke(self, data, user):
        payload = self._payload(data)
        grant_id = str(payload.get("grant_id") or "")
        if not self.grant_store.delete(self._org(user), grant_id):
            return self._fail(data, "not_found", "Grant not found.", 404)
        AuditStore().append(
            "agent.revoked", user, resource_refs=[grant_id],
            details={"agent_ref": str(payload.get("agent_id") or "")},
        )
        return self._respond(data, {"revoked": True})
