"""What is on offer, and what installing it would mean.

The read side of the marketplace: `available` lists this
organization's approved agents with what is left to do about each one;
`_review` is what an administrator agreed to, spelled out.
"""

from contracts.agent_manifest import Manifest, manifest_hash


class AgentCatalogMixin:

    async def available(self, data, user):
        """This organization's approved agents, and what is left to do
        about each one.

        Answered from the database alone. It used to ask a runtime what
        it was holding and reconcile the two, which cannot mean anything
        once there is more than one runtime process: each holds what its
        own chats have pulled, so an agent absent from the one that
        answered is normal rather than missing, and one present but
        unapproved is another organization's code. Whether a package
        loads is what a runtime reports once it has built it
        (``prepared``).

        So there are three states, and all are facts this side owns:
        installed; installed with a newer commit waiting at the source
        it came from; and installed while the source's catalog has
        stopped listing it — renamed or withdrawn upstream — which is
        not an update anybody can take, only something to uninstall or
        replace."""
        sources = {
            str(doc["_id"]): doc
            for doc in self.source_store.list_for(user)
        }
        granted = self.secret_grant_store.granted_map(self._org(user))

        agents = []
        for approved in self.store.list(self._org(user)):
            source = approved.get("source") or {}
            catalog = sources.get(str(approved.get("source_id") or ""))
            latest = str((catalog or {}).get("last_resolved_sha") or "")
            pinned = str(source.get("sha") or "")
            # This agent's manifest in the source's refreshed catalog —
            # what updating would install. A catalog holds many agents
            # in one repository, so the repository moving on says
            # nothing about THIS agent: an update exists only when its
            # own manifest differs from the one approved, and a version
            # is immutable, so that means a new version. Absent when
            # the catalog no longer lists it at all.
            waiting = self._waiting_manifest(catalog, approved)
            samples = self._waiting_samples(catalog, approved)
            if samples is None:
                samples = approved.get("samples")
            moved = bool(latest and pinned and latest != pinned)
            changed = waiting is not None and manifest_hash(waiting) != (
                approved.get("manifest_hash") or manifest_hash(approved.get("manifest") or {}))
            agents.append({
                **self._review(approved.get("manifest") or {}),
                "agent_id": approved["_id"],
                "status": ("update_available" if changed
                           else "removed" if moved and waiting is None
                           else "installed"),
                "installed_version": approved.get("version"),
                "loaded_version": self._version_of(waiting),
                # What a runtime said about this version being ready to
                # run; None until one has spoken.
                "prepared": self._readiness(approved),
                # What the catalog says this agent's samples would load;
                # the samples endpoint reads the approved package itself.
                "samples": samples,
                "source": source,
                "source_name": str((catalog or {}).get("name") or ""),
                # The commit waiting at the source, when it is not the
                # one this approval pins. Empty otherwise: an update
                # nobody can act on is not news.
                "available_sha": latest if latest != pinned else "",
                # The canonical ids approval derived — what a credential
                # for this agent must actually be created from.
                "resource_refs": approved.get("resources") or {},
                # Slots a saved credential was granted to: satisfied
                # without a secret of the agent's own existing.
                "granted_secrets": granted.get(approved["_id"], []),
            })

        agents.sort(key=lambda item: (item.get("name") or item["agent_id"]))
        return self._respond(data, {"agents": agents})

    @staticmethod
    def _waiting_manifest(source_doc, approved):
        """This agent's manifest in the source's refreshed catalog, or
        None when the catalog no longer lists it."""
        local_id = str(approved.get("local_agent_id") or "")
        path = str((approved.get("source") or {}).get("catalog_path") or ".")
        for entry in ((source_doc or {}).get("catalog") or {}).get(
                "agents") or []:
            if (str(entry.get("id") or "") == local_id
                    and str(entry.get("path") or ".") == path):
                return entry.get("manifest") or {}
        return None

    @staticmethod
    def _waiting_samples(source_doc, approved):
        local_id = str(approved.get("local_agent_id") or "")
        for entry in ((source_doc or {}).get("catalog") or {}).get("agents") or []:
            if str(entry.get("id") or "") == local_id:
                return entry.get("samples")
        return None

    @staticmethod
    def _version_of(document):
        if not document:
            return ""
        return str((document.get("agent") or {}).get("version") or "")

    @staticmethod
    def _review(document):
        """What an administrator is being asked to approve: what the
        agent can do, what it will hold, and what it wants installed."""
        if not document:
            return {
                "name": "", "description": "",
                "functions": [], "resources": {}, "dependencies": [],
                "network": {"declared": False, "any": False,
                            "hosts": [], "from_secrets": []},
            }
        manifest = Manifest(document)
        agent = document.get("agent") or {}
        return {
            "name": agent.get("name", ""),
            "description": agent.get("description", ""),
            # What to try first, as the manifest declared it.
            "examples": manifest.examples(),
            "functions": [
                {
                    "name": name,
                    "tool": tool.get("id", ""),
                    "description": function.get("description", ""),
                    "permission_level": function.get("permission_level"),
                    "timeout_seconds": function.get("timeout_seconds"),
                    # Which functions think with the deployment's model is
                    # part of what approval agrees to.
                    "llm": function.get("llm") is True,
                }
                for name, tool, function in manifest.functions()
            ],
            "resources": {
                kind: [
                    {
                        "id": resource.get("id", ""),
                        "label": resource.get("label", ""),
                        "description": resource.get("description", ""),
                        "fields": [
                            field.get("name", "")
                            for field in resource.get("fields") or []
                        ],
                    }
                    for resource in manifest.resources(kind)
                ]
                for kind in ("secrets", "data", "files")
            },
            "dependencies": list(manifest.dependencies),
            # Where it connects is part of what approval agrees to — and
            # so is reaching, for one run, what a person allows on a
            # code card (a function with `code: true`).
            "network": {**manifest.network,
                        **({"proposed": True} if manifest.runs_code else {})},
        }
