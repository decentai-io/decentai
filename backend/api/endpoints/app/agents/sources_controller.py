"""Saved agent sources: the roster of places code comes from.

A source is a repository this organization saved, and the Marketplace
lists and installs from it. Creating, refreshing, editing and deleting
them is this file; what a source IS lives in
api/services/agents/sources.py.

A source never leaves its organization. Two organizations wanting the
same repository each save it, so neither holds the other's supply of
code.
"""


from api.services.agents.acquisition import AcquisitionError
from api.services.agents.sources import GitSource
from database.stores import AuditStore
from server.governance import INFRASTRUCTURE, Sharing

#: The sharing rules a saved source answers to.
SOURCE_SHARING = Sharing(INFRASTRUCTURE, "source")
from util import iso
from pymongo.errors import DuplicateKeyError




class AgentSourcesMixin:

    def _public_source(self, doc, user):
        """One source, as the page reads it. Every source a caller can
        reach is their own organization's."""
        creator = str(doc.get("created_by_id") or "")
        mine = bool(creator) and creator == str(user.get("user_id") or "")
        owned = mine or self._may_manage_any(user)
        owner = doc.get("owner") or {}
        return {"source_id": doc["_id"], "name": doc.get("name", ""),
                # Whether the caller created it — the badge; `owned` is
                # whether they may change it, which the manage-any grant
                # widens to every source of the organization.
                "mine": mine,
                "owner": {
                    "groups": list(owner.get("groups") or []),
                    "users": list(owner.get("users") or []),
                },
                "created_by_id": creator,
                "url": doc.get("url", ""), "ref": doc.get("ref", ""),
                "owned": owned,
                # Whether a token is stored, and under which account —
                # never the token. Editing shows the username back and a
                # blank password box, exactly like every other rotation.
                "has_credential": (self.source_store.has_credential(doc)
                                   if owned else False),
                "credential_user": (doc.get("credential_user", "")
                                    if owned else ""),
                "status": doc.get("status", "new"),
                "sha": doc.get("last_resolved_sha", ""),
                "catalog": self._annotated(doc, user),
                # Anyone shown it may refresh, so anyone shown it may
                # read why the last read failed.
                "last_error": doc.get("last_error", ""),
                # What stands between this source and removal, so the
                # page can say so before the button is pressed.
                "installed_agents": self.store.installed_from_source(
                    self._org(user), doc["_id"]),
                "last_checked_at": iso(doc.get("last_checked_at"))}

    def _annotated(self, doc, user):
        """The stored catalog with what THIS organization has done to it.

        The catalog is a snapshot of somebody else's repository; which of
        its agents we have approved is our own business and changes
        without it. Read together here rather than written into the
        snapshot, so installing does not leave the copy stale."""
        catalog = doc.get("catalog")
        if not catalog:
            return catalog
        annotated = []
        for entry in catalog.get("agents") or []:
            ref = self.store.existing_ref(
                self._org(user), doc["_id"], str(entry.get("id") or ""))
            installed = self.store.get_in(self._org(user), ref) if ref else None
            annotated.append({**entry, "agent_ref": ref,
                              "installed_version": (installed or {}).get("version")})
        return {**catalog, "agents": annotated}

    def sources(self, data, user):
        """The saved sources, and the one this deployment is willing to
        suggest. The suggestion is configuration, never a default: a
        platform that shipped pointing at its vendor's repository would be
        making that choice for every deployment."""
        from server.setup.app_state import get_state

        settings = get_state().settings
        return self._respond(data, {
            "sources": [self._public_source(doc, user)
                        for doc in self.source_store.list_for(user, escape=self._may_manage_any(user))],
            "reference_catalog_url": getattr(
                settings, "reference_catalog_url", "") or "",
        })

    async def sourcecreate(self, data, user):
        payload = self._payload(data)
        url = str(payload.get("url") or "").strip()
        if not url:
            return self._fail(data, "invalid_request", "A repository URL is required.")
        name = str(payload.get("name") or "").strip() or url.rstrip("/").split("/")[-1]
        refusal = SOURCE_SHARING.reach_refusal(user, payload.get("owner"))
        if refusal:
            body, status = refusal
            return self._fail(data, "owner_out_of_reach", body["error"], status)
        try:
            doc = self.source_store.create(
                user, name=name[:100], url=url,
                ref=str(payload.get("ref") or "").strip(),
                credential=self._inline_credential(payload),
                owner=payload.get("owner"))
        except ValueError as exc:
            return self._fail(data, "invalid_request", str(exc))
        except DuplicateKeyError:
            return self._fail(data, "source_exists", "That repository source is already saved.", 409)
        AuditStore().append("agent_source.created", user,
                            resource_refs=[doc["_id"]], details={"url": url})
        return await self._refresh_source(data, user, doc)

    async def sourcerefresh(self, data, user):
        """Read the catalog again. Open to anyone in the organization the
        source is shown to, not only its creator: the snapshot is what
        everybody installs from, and a colleague waiting on a new agent
        version should not need the person who saved the source to be
        around. A source saved with a credential is read with it on the
        colleague's behalf — inside one organization, showing somebody a
        source is showing them its catalog, however it is fetched.
        Editing and deleting stay the creator's."""
        doc = self.source_store.get_for(
            user, str(self._payload(data).get("source_id") or ""))
        if doc is None:
            return self._not_found(data)
        return await self._refresh_source(data, user, doc)

    def _not_found(self, data):
        """Not yours, or not there — the same answer either way."""
        return self._fail(data, "not_found", "Agent source not found.", 404)

    #: The administrator's escape from the creator-only rule.
    SOURCE_MANAGE_ANY = "agents:agent:source_manage_any"

    def _may_manage_any(self, user) -> bool:
        return self.policy.is_allowed(user, self.SOURCE_MANAGE_ANY)

    def _manageable(self, user, source_id):
        """The source this caller may change: their own, or any of the
        organization's with the grant."""
        return self.source_store.owned_by(
            user, str(source_id or ""), escape=self._may_manage_any(user))

    async def sourceupdate(self, data, user):
        payload = self._payload(data)
        doc = self._manageable(user, payload.get("source_id"))
        if doc is None:
            return self._not_found(data)
        changes = {key: str(payload.get(key) or "").strip()
                   for key in ("name", "url", "ref") if key in payload}
        if "url" in changes and not changes["url"]:
            return self._fail(data, "invalid_request", "A repository URL is required.")
        if "owner" in payload:
            refusal = SOURCE_SHARING.reach_refusal(user, payload.get("owner"))
            if refusal:
                body, status = refusal
                return self._fail(
                    data, "owner_out_of_reach", body["error"], status)
            try:
                cleaned = SOURCE_SHARING.clean(
                    payload.get("owner"),
                    creator=str(doc.get("created_by_id")
                                or user.get("user_id") or ""))
                SOURCE_SHARING.check_exists(self._org(user), cleaned)
            except ValueError as exc:
                return self._fail(data, "invalid_request", str(exc))
            self.source_store.set_owner(doc["_id"], cleaned)
        if "credential" in payload:
            if payload.get("credential") is None:
                # Explicitly public again: the token goes, not just the
                # username beside it.
                self.source_store.clear_credential(doc["_id"])
            elif isinstance(payload.get("credential"), dict):
                cred = payload.get("credential") or {}
                self.source_store.set_credential(
                    doc["_id"], cred.get("username"), cred.get("token"))
        try:
            self.source_store.edit(doc["_id"], changes)
        except DuplicateKeyError:
            # Pointing this source at a repository already saved here.
            return self._fail(
                data, "source_exists",
                "That repository is already saved as another source.", 409)
        AuditStore().append("agent_source.updated", user, resource_refs=[doc["_id"]])
        return await self._refresh_source(data, user,
            self.source_store.get_for(user, doc["_id"]))

    async def _refresh_source(self, data, user, doc):
        try:
            found = self.acquisition.inspect(GitSource.recorded(
                doc, self.source_store.credential_of(doc)))
            catalog = found["catalog"]
            self.source_store.record_catalog(
                doc["_id"], catalog, found.get("sha", ""))
            AuditStore().append("agent_source.refreshed", user,
                                resource_refs=[doc["_id"]],
                                details={"sha": found.get("sha", ""),
                                         "agents": len(catalog.get("agents") or [])})
        # AcquisitionError is the one that actually happens here — a
        # typo'd URL, a private repository with no credential, a branch
        # that does not exist. It was not caught, so the single most
        # likely outcome of saving a source was a 500 and a row stuck at
        # status "new" with an empty last_error: the field the page shows
        # was dead for exactly the case it exists to explain.
        except AcquisitionError as exc:
            self.source_store.record_failure(doc["_id"], str(exc))
        return self._respond(data, {"source": self._public_source(
            self.source_store.get_for(user, doc["_id"]), user)})

    async def sourcepurge(self, data, user):
        """Uninstall every agent installed from a source, then remove
        it — the one-click answer to the refusal below."""
        doc = self._manageable(user, self._payload(data).get("source_id"))
        if doc is None:
            return self._not_found(data)
        uninstalled = []
        for entry in self.store.installed_from_source(self._org(user), doc["_id"]):
            agent = self.store.get_in(self._org(user), entry["agent_ref"])
            if agent is not None:
                self._uninstall(agent, user)
                uninstalled.append(entry["name"])
        self.source_store.delete(doc["_id"])
        AuditStore().append("agent_source.deleted", user,
                            resource_refs=[doc["_id"]],
                            details={"uninstalled": uninstalled})
        return self._respond(data, {"deleted": True, "uninstalled": uninstalled})

    def sourcetransfer(self, data, user):
        """Hand a source to another member: the creator's act, or a
        manage-any holder's."""
        from api.services.successor import successor_or_error

        payload = self._payload(data)
        doc = self._manageable(user, payload.get("source_id"))
        if doc is None:
            return self._not_found(data)
        successor, why = successor_or_error(
            self._org(user), payload.get("user_id"),
            excluding=str(doc.get("created_by_id") or ""))
        if successor is None:
            return self._fail(data, "invalid_request", why)
        self.source_store.set_creator(doc["_id"], successor["_id"])
        AuditStore().append("agent_source.transferred", user,
                            resource_refs=[doc["_id"]],
                            details={"to": successor.get("email", "")})
        return self._respond(data, {"source": self._public_source(
            self.source_store.get_for(user, doc["_id"]) or
            self.source_store.owned_by(user, doc["_id"], escape=True), user)})

    def sourcedelete(self, data, user):
        doc = self._manageable(user, self._payload(data).get("source_id"))
        if doc is None:
            return self._not_found(data)

        # Refused, not warned. This document holds the map from the
        # repository's local ids to the platform refs its agents are
        # installed under — including the prefix of every secret
        # definition derived for them. Deleting it while they are
        # installed strands that identity for good: the same repository
        # added again mints fresh refs and can never be matched to what
        # was already approved.
        installed = self.store.installed_from_source(self._org(user), doc["_id"])
        if installed:
            names = ", ".join(agent["name"] for agent in installed)
            return self._fail(
                data, "source_in_use",
                f"Uninstall this source's agents first: {names}. Removing "
                f"the source while they are installed would strand them "
                f"from the catalog they came from.",
                409,
            )

        self.source_store.delete(doc["_id"])
        AuditStore().append("agent_source.deleted", user,
                            resource_refs=[doc["_id"]])
        return self._respond(data, {"deleted": True})
