"""Approving an agent, and everything the approval sets in motion.

The longest path in the platform, on purpose: an install derives the
secret definitions the manifest declares, mints or reuses the approval's
identity, and acquires and stores the package. Uninstalling withdraws
the approval and takes with it everything that only existed to serve it
— grants, credential grants, unused shapes, unreachable packages.
"""

from api.services.agents.acquisition import AcquisitionError
from api.services.agents.sources import GitSource
from contracts.agent_manifest import Manifest, ManifestValidator, manifest_hash
from database.stores import AuditStore
from database.stores.data.secrets import SecretStore


class AgentInstallMixin:

    async def install(self, data, user):
        payload = self._payload(data)
        url = str(payload.get("url") or "").strip()
        source_id = str(payload.get("source_id") or "")

        # Naming an installed agent alone means UPDATE IT: everything an
        # update needs — the source, which entry, which folder — is on
        # the approval, and asking the page to repeat it back would only
        # let the two disagree.
        asked = str(payload.get("agent_id") or "")
        if asked and not source_id and not url:
            approved = self.store.get_in(self._org(user), asked)
            if approved is None:
                return self._fail(
                    data, "not_found", "Agent is not installed.", 404)
            source_id = str(approved.get("source_id") or "")
            payload["local_agent_id"] = str(
                approved.get("local_agent_id") or "")
            payload["catalog_path"] = str(
                (approved.get("source") or {}).get("catalog_path") or ".")

        # Where a saved source points is the platform's to read, not the
        # caller's to assert: naming the id is the whole request, and the
        # URL, ref and credential come from the record. `get_for` reads a
        # source of this organization's that the caller is shown, and
        # nothing else — a source never crosses an organization.
        if source_id:
            doc = self.source_store.get_for(user, source_id)
            if doc is None:
                return self._fail(
                    data, "not_found", "That agent source is not available.",
                    404)
            url = str(doc.get("url") or "")
            payload["ref"] = str(doc.get("ref") or "")
            # The credential is this organization's own by construction —
            # resolved from the source document, in process, never from
            # the request.
            payload["credential"] = self.source_store.credential_of(doc)

        if not url:
            # There is no second way in. An agent is approved from a place
            # this organization named — never by pointing at code the
            # runtime happens to be holding, which is somebody else's
            # approval on a shared volume.
            return self._fail(
                data, "invalid_request",
                "Name the source to install from.",
            )

        # What gets approved is the manifest at one specific commit,
        # fetched now and pinned into the record.
        try:
            credential = self._inline_credential(payload)
            found = self.acquisition.inspect(GitSource(
                url, str(payload.get("ref") or "").strip(), credential))
        except AcquisitionError as exc:
            return self._fail(data, "fetch_failed", str(exc))
        local_agent_id = str(payload.get("local_agent_id") or "").strip()
        catalog_path = str(payload.get("catalog_path") or ".").strip()
        entries = found["catalog"]["agents"]
        selected = next((entry for entry in entries
                         if str(entry.get("id")) == local_agent_id
                         and str(entry.get("path") or ".") == catalog_path), None)
        # A repository with a single manifest needs no id to install from
        # — but only on a fresh install. An UPDATE names the agent it
        # holds, and the only entry being a different agent means this
        # one was renamed or withdrawn, not that it moved. Falling
        # through here re-approved one agent as another.
        if selected is None and len(entries) == 1 and not local_agent_id:
            selected = entries[0]
            local_agent_id = str(selected.get("id") or "")
            catalog_path = str(selected.get("path") or ".")
        if selected is None:
            return self._fail(data, "not_found",
                              "This agent is no longer in the catalog at its source — it was "
                              "renamed or withdrawn upstream. Uninstall it and install its "
                              "replacement from the marketplace.", 404)
        document = selected.get("manifest") or {}

        if not source_id:
            # Approving a URL directly rather than from a saved catalog:
            # the place gets saved too, so this install can be recognised
            # — and updated — later.
            source_id = self.source_store.find_or_create(
                user, url=url, ref=str(payload.get("ref") or "").strip(),
                credential=self._inline_credential(payload),
                name=str(found["catalog"].get("catalog", {}).get("name") or ""),
            )["_id"]
        # The ref is the platform's to decide, not the caller's: the one
        # this organization already installed this agent under, else a
        # fresh one.
        platform_agent_id = self.store.ref_for(
            self._org(user), source_id, local_agent_id)
        source = {
            "type": GitSource.kind,
            "url": url,
            "ref": str(payload.get("ref") or "").strip(),
            "sha": str(found.get("sha") or ""),
            "catalog_path": catalog_path,
        }

        errors = ManifestValidator().validate(document)
        if errors:
            return self._fail(
                data, "invalid_manifest",
                "Manifest validation failed: " + "; ".join(errors[:10]),
            )

        manifest = Manifest(document)
        agent_id = platform_agent_id
        existing = self.store.get_in(self._org(user), agent_id)
        if existing is not None and existing["version"] == manifest.version:
            if existing["manifest"] != document:
                return self._fail(
                    data, "immutable_version",
                    f"Version {manifest.version} of '{agent_id}' is already "
                    f"approved with different content — publish a new version.",
                    409,
                )
            # Installing a repository again is a repair rather than a
            # no-op: the approval can stand while the code is gone (a
            # recreated container), and the ref may now point at a
            # different commit that carries the same manifest.

        resource_namespace = platform_agent_id
        derived_secrets = self._derive_secret_definitions(
            manifest, user, resource_namespace
        )
        resources = {
            "secrets": derived_secrets,
            "data": {
                resource["id"]: f"{resource_namespace}__{resource['id']}"
                for resource in manifest.resources("data")
            },
            "files": {
                resource["id"]: f"{resource_namespace}__{resource['id']}"
                for resource in manifest.resources("files")
            },
        }

        digest = manifest_hash(document)
        previous = self.store.get_in(self._org(user), agent_id)
        # The name a policy can be written in. Assigned here, once, and
        # kept for the life of the install.
        source_name = str(
            (self.source_store.get(source_id) or {}).get("name") or "")
        qualified_id = self.store.assign_qualified_id(
            self._org(user),
            agent_id,
            local_agent_id or manifest.agent_id,
            source_name,
        )

        stored = self.store.upsert(
            self._org(user),
            agent_id, manifest.version, document, resources,
            str(user.get("email") or ""),
            source=source, manifest_hash=digest, qualified_id=qualified_id,
            source_id=source_id, local_agent_id=local_agent_id,
            samples=selected.get("samples"),
        )

        # Now, and only now that the manifest is approved, the code
        # is fetched and stored. If that fails the approval is put
        # back as it was. Installing is a backend act, deliberately:
        # no runtime is asked anything, and whether the code LOADS
        # is discovered when a chat first pulls it — the runtime's
        # own honest report, at the moment it matters.
        #
        # Nothing is sent to a runtime to KEEP. Code reaches a
        # runtime when a chat of this organization connects and it
        # pulls what the approval names — which is the only thing
        # that works when there is more than one of them.
        try:
            archive, package_digest, _ = self.acquisition.acquire(
                self._org(user),
                GitSource.recorded(source, credential),
                local_agent_id or manifest.agent_id,
                source.get("catalog_path", "."),
            )
            # Stored BEFORE it is served: the store is what answers
            # for this code from now on, and an agent running from a
            # package nobody kept is the situation this replaces.
            self.store.set_package_digest(agent_id, package_digest)
        except AcquisitionError as exc:
            if previous is None:
                self.store.delete(agent_id)
            else:
                self.store.put_back(previous)
            return self._fail(data, "activation_failed", str(exc))
        stored = self.store.get_in(self._org(user), agent_id)

        self._grant_on_install(user, agent_id)

        # An upgrade just repointed this approval at a new digest, which
        # strands the old one on disk. Reclaim whatever no approval of
        # this organization points at any more.
        self.acquisition.reclaim(
            self._org(user), self.store.pinned_digests(self._org(user)))

        # The credentials saved under this agent's shapes follow it to
        # the shapes it now reads, wherever that asks nothing new of
        # their owners (SecretStore.move_forward). Then the superseded
        # versions nothing stands on go — the new approval pins the
        # current one, a secret that could not follow protects its own,
        # and nothing else is left behind by an update.
        moved = left = 0
        for ref in (resources.get("secrets") or {}).values():
            target = self.definitions.get_in(self._org(user), str(ref))
            if target is not None:
                followed, stayed = SecretStore().move_forward(
                    self._org(user), target)
                moved, left = moved + followed, left + stayed
            self.definitions.prune_unused(
                self._org(user),
                str(ref).rpartition(":")[2].partition("/v")[0])
        if moved or left:
            self.logger.info(
                f"{agent_id}: {moved} credential(s) moved to the new shape, "
                f"{left} left for their owners to complete")

        AuditStore().append(
            "agent.installed", user, resource_refs=[agent_id],
            details={"version": manifest.version, "source": source["type"],
                     "package_digest": stored.get("package_digest", "")},
        )
        if self._prepare(user, stored):
            # A runtime is building it now; the page says so until the
            # runtime reports back (Agents:Agent:Prepared).
            stored["prepared"] = self.store.set_prepared(
                stored["_id"], stored.get("package_digest"), "preparing")
        return self._respond(data, {
            "agent": self._summary(stored), "installed": True,
        })

    #: Prepare frames on their way, held so a task is not collected
    #: before it runs.
    _preparing: set = set()

    def _prepare(self, user, stored):
        """The runtime pulls the package and builds its environment
        now, not when the first chat names it: the person who clicked
        Install or Update should not meet a minute of pip in their next
        chat. The runtime is reached the way everything reaches it,
        through a chat's dial with that chat's delegation — the
        person's most recent chat, dialed if it is not up. Nobody
        without a chat is left waiting for anything: their first chat
        prepares it as before. Never a failure of the install: the
        store is what answers for the approval either way. True when a
        runtime was sent to build it."""
        try:
            import asyncio

            from database.stores import ChatStore
            from server.setup.app_state import get_runtime_clients

            chats = ChatStore().list_for(
                str(user.get("org_id") or ""), str(user.get("user_id") or ""))
            chat = next((c for c in chats if c.get("status") != "archived"), None)
            if chat is None or not stored.get("package_digest"):
                return False
            manifest = stored.get("manifest") or {}
            frame = {"event": "agents_changed", "agents": [{
                "agent_id": str(stored.get("_id") or ""),
                "name": str((manifest.get("agent") or {}).get("name") or ""),
                "package_digest": str(stored.get("package_digest") or ""),
                "manifest_hash": str(stored.get("manifest_hash") or ""),
            }]}
            task = asyncio.get_running_loop().create_task(
                get_runtime_clients().send(str(chat["chat_id"]), user, frame))
            self._preparing.add(task)
            task.add_done_callback(self._preparing.discard)
            return True
        except Exception as exc:  # noqa: BLE001 — never a failed install
            self.logger.warning(
                f"Could not ask a runtime to prepare {stored.get('_id')}: {exc}")
            return False

    def _derive_secret_definitions(self, manifest, user, namespace=None):
        """Secret declarations → versioned data-layer definitions. Returns
        {resource_id: exact definition_ref} — the approved mapping.

        Every declaration derives an agent-PRIVATE definition, namespaced
        by the approval's ref so two agents' ``connection`` never collide
        and no agent can land on a name somebody else's credential is
        stored under. That namespacing is also what lets the backend tell
        which agent is asking at use time: the category names exactly one
        approval.

        Declaring a shape is not access to anything. A credential filled
        in elsewhere reaches this agent only when somebody grants it
        (AgentSecretGrantStore), which is a decision that can be revoked
        rather than a name an agent chose for itself."""
        mapping = {}
        for resource in manifest.resources("secrets"):
            slug = f"{namespace or manifest.agent_id}__{resource['id']}"
            fields = [
                {
                    "name": field["name"],
                    "label": field.get("label") or field["name"],
                    "type": field["type"],
                    "storage": field.get("storage", "values"),
                    "required": bool(field.get("required")),
                    **({"options": field["options"]} if field.get("options") else {}),
                }
                for field in resource.get("fields") or []
            ]
            label = resource.get("label") or slug
            description = resource.get("description") or ""
            oauth = resource.get("oauth")

            existing = self.definitions.latest(self._org(user), slug)
            if existing is None:
                self.definitions.create_family(
                    user, slug, label, description, fields, oauth=oauth
                )
            else:
                try:
                    self.definitions.add_version(
                        user, slug, label, description, fields, oauth=oauth
                    )
                except ValueError:
                    pass  # identical content — the recorded version stands
            mapping[resource["id"]] = self.definitions.latest(
                self._org(user), slug)["_id"]
        return mapping

    async def delete(self, data, user):
        agent_id = str(self._payload(data).get("agent_id") or "")
        doc = self.store.get_in(self._org(user), agent_id)
        if doc is None:
            return self._fail(data, "not_found", "Agent is not installed.", 404)
        self._uninstall(doc, user)
        return self._respond(data, {"deleted": True})

    def _uninstall(self, doc, user):
        """Withdraw one approval and everything that stood on it. The
        cascade behind Delete, callable for a source being purged."""
        agent_id = str(doc.get("_id") or "")
        # Versions backing live secrets survive; only the approval is
        # withdrawn — and, below, the shapes nothing else stands on.
        self.store.delete(agent_id)
        # Grants do NOT survive. They exist only to say who may call this
        # agent, so an uninstall takes them with it rather than leaving
        # rows that name nothing — which is the rot that kept these out
        # of IAM policies in the first place.
        revoked = self.grant_store.delete_for_agent(self._org(user), agent_id)
        # Same reasoning for the credentials handed to it: a grant names
        # this agent, so it goes when the agent does. The secrets stay —
        # what is withdrawn is the reach, not the data.
        credentials = self.secret_grant_store.delete_for_agent(
            self._org(user), agent_id)
        # Sample data somebody loaded stays theirs — ordinary records now;
        # only the memory of it as samples has nothing left to do.
        from database.stores import AgentSampleStore
        AgentSampleStore().forget_agent(self._org(user), agent_id)
        # With the pin gone, this agent's shapes are prunable wherever no
        # credential was saved under them.
        for ref in ((doc.get("resources") or {}).get("secrets") or {}).values():
            self.definitions.prune_unused(
                self._org(user),
                str(ref).rpartition(":")[2].partition("/v")[0])

        # The stored code goes with the approval — per organization, so
        # another organization's copy of the same bytes is untouched. The
        # reachable set is computed AFTER the delete above, so a digest a
        # remaining agent still pins survives.
        packages = self.acquisition.reclaim(
            self._org(user), self.store.pinned_digests(self._org(user)))

        # The runtime is not told. Its copy of the code is named by a
        # digest and may be serving other organizations that approved the
        # same agent; this organization's chats simply stop being told
        # they may call it, which is what withdrawal means.
        AuditStore().append(
            "agent.deleted", user, resource_refs=[agent_id],
            details={"version": doc.get("version"), "grants_revoked": revoked,
                     "credentials_revoked": credentials,
                     "packages_reclaimed": packages},
        )
