"""Which saved credential an agent may use.

An agent's manifest declares the SHAPE of a credential it needs, and
installing derives a private definition for it. That declaration is a
description, never a claim: a credential filled in somewhere else reaches
the agent only when somebody grants it here.

The rule on a grant is that the shapes agree. An agent said what it
expects to be handed; a credential of a different shape is not that,
however plausibly it is named. A connected account carries two more
facts the shape does not: which provider signed it, and which scopes the
person consented to. A Google account is not a Microsoft one whatever
its fields look like, and an account consented for mail alone cannot be
handed to an agent that declared files as well. Everything else — who
may see the secret, who may see the agent — is already decided elsewhere
and is not re-litigated here.

Lending to many is the same act repeated: one connected account, every
installed agent of the same provider that could take it, one grant row
each, one audit event each. Nothing here is a new kind of access.
"""


from database.stores.data.definitions import DefinitionStore
from database.stores.data.secrets import SecretStore
from database.stores import AgentSecretGrantStore, AuditStore


class AgentSecretGrantsMixin:

    @staticmethod
    def _family(ref) -> str:
        """A definition ref without its version — the name a slot's own
        credentials are saved under, whichever version pinned them."""
        return str(ref or "").partition("/v")[0]

    def _declared_secret(self, agent, resource_id: str):
        """The agent's own declaration for one slot, from the approved
        manifest — what it said it expects, not what it later asks for."""
        for resource in (agent.get("manifest") or {}).get(
                "resources", {}).get("secrets") or []:
            if str(resource.get("id") or "") == resource_id:
                return resource
        return None

    @staticmethod
    def _own_ref(agent, resource_id: str) -> str:
        """The definition installing derived for one of the agent's slots."""
        return str((agent.get("resources") or {}).get(
            "secrets", {}).get(resource_id, ""))

    def _slot_shape(self, org_id: str, definition_ref: str, declared=None):
        """What a slot really expects: the definition installing derived
        for it, not the manifest's raw declaration. The two differ for a
        connected account, which declares no fields of its own and gets
        the platform's five — so comparing a credential against the raw
        declaration refused every connected account ever offered.

        Returns (fields, provider, scopes, definition_id); provider and
        scopes are empty for a typed-in credential, which no provider
        signed."""
        definition = (self.definitions.get_in(org_id, definition_ref)
                      if definition_ref else None) or {}
        declared = declared or {}
        oauth = definition.get("oauth") or declared.get("oauth") or {}
        fields = definition.get("fields") or declared.get("fields") or []
        return (fields, str(oauth.get("provider") or ""),
                {str(s) for s in oauth.get("scopes") or []},
                str(definition.get("definition_id") or ""))

    def _still_fits(self, org_id: str, own_ref: str, secret_ref: str) -> bool:
        """Whether a lent credential is still of the shape its slot
        expects. One that is gone fits nothing."""
        from api.services.data_layer.secrets import SecretController

        secret = SecretStore().get(secret_ref)
        # The slot's shape as the use door reads it: its family's
        # current version.
        slug = str(own_ref).rpartition(":")[2].partition("/v")[0]
        slot = self.definitions.latest(org_id, slug) if slug else None
        if secret is None or slot is None:
            return secret is not None
        lent = self.definitions.get_in(
            org_id, str(secret.get("definition_ref") or "")) or {}
        return SecretController.fits(slot, lent)

    def secretgrants(self, data, user):
        """Every slot this agent declared: what is granted to it now, and
        which of the caller's own credentials could be.

        Candidates come from what the CALLER can see. A grant cannot
        hand over a secret the person making it could not read
        themselves, so the list is built from their visibility rather
        than from the collection."""
        agent_id = str(self._payload(data).get("agent_id") or "")
        org_id = self._org(user)
        agent = self.store.get_in(org_id, agent_id)
        if agent is None:
            return self._fail(data, "not_found", "Agent is not installed.", 404)

        granted = {
            doc["resource_id"]: AgentSecretGrantStore.to_public(doc)
            for doc in self.secret_grant_store.for_agent(org_id, agent_id)
        }
        for resource_id, grant in granted.items():
            # Lent when it fitted; the agent's update may have changed
            # what the slot expects since, and the agent is then
            # refused it at use (Secrets:Secret:Use). Said here too.
            grant["fits"] = self._still_fits(
                org_id, self._own_ref(agent, resource_id),
                str(grant.get("secret_ref") or ""))
            # What the page calls it: a credential that no longer fits
            # is not among the candidates the page could look it up in.
            lent = SecretStore().get(str(grant.get("secret_ref") or "")) or {}
            grant["secret_name"] = str(lent.get("name") or "")

        secrets = SecretStore()
        definitions = self.definitions
        visible = secrets.list_visible(user)
        # One read per distinct definition, not per secret: a person with
        # forty credentials on one definition should not cost forty
        # lookups to answer one question.
        shapes: dict = {}
        providers: dict = {}
        for secret in visible:
            ref = str(secret.get("definition_ref") or "")
            if ref and ref not in shapes:
                found = definitions.get_in(org_id, ref) or {}
                shapes[ref] = found.get("fields") or []
                providers[ref] = str((found.get("oauth") or {}).get("provider") or "")

        slots = []
        for resource in (agent.get("manifest") or {}).get(
                "resources", {}).get("secrets") or []:
            resource_id = str(resource.get("id") or "")
            own_ref = self._own_ref(agent, resource_id)
            declared, provider, _, _ = self._slot_shape(org_id, own_ref, resource)
            slots.append({
                "resource_id": resource_id,
                "label": resource.get("label") or resource_id,
                "description": resource.get("description") or "",
                "definition_ref": own_ref,
                "grant": granted.get(resource_id),
                # A credential saved under this slot's OWN definition
                # already reaches the agent without a grant, so offering
                # it here would be a button that does nothing. And every
                # connected account has the same five fields, so the
                # shape alone would offer a Google account to a Microsoft
                # slot: the provider has to agree too.
                "candidates": [
                    {"resource_ref": secret.get("resource_ref", ""),
                     "name": secret.get("name", ""),
                     "definition_ref": secret.get("definition_ref", "")}
                    for secret in visible
                    if self._family(secret.get("definition_ref"))
                    != self._family(own_ref)
                    and providers.get(
                        str(secret.get("definition_ref") or ""), "") == provider
                    and not DefinitionStore.shape_mismatch(
                        declared, shapes.get(
                            str(secret.get("definition_ref") or ""), []))
                ],
            })
        return self._respond(data, {"slots": slots})

    # ------------------------------------------------------------------
    def _grant_one(self, user, org_id: str, agent, resource_id: str,
                   secret, secret_ref: str):
        """One credential into one slot, or the reason it cannot go there.
        Returns (public grant, None) or (None, (code, message, status))."""
        agent_id = str(agent["_id"])
        declared = self._declared_secret(agent, resource_id)
        if declared is None:
            return None, (
                "not_found",
                f"'{agent.get('qualified_id') or agent_id}' declares no "
                f"credential called '{resource_id}'.", 404)

        # Its own credential needs no grant — refusing keeps the grants
        # list meaning something: every row is access somebody ADDED.
        own_ref = self._own_ref(agent, resource_id)
        if self._family(secret.get("definition_ref")) == self._family(own_ref):
            return None, (
                "already_its_own",
                f"'{secret.get('name')}' was created under this agent's own "
                f"'{resource_id}' slot — it already reaches the agent "
                f"without a grant.", 409)

        definition = self.definitions.get_in(
            org_id, str(secret.get("definition_ref") or ""))
        wanted_fields, wanted_provider, wanted_scopes, _ = self._slot_shape(
            org_id, own_ref, declared)
        mismatched = DefinitionStore.shape_mismatch(
            wanted_fields, (definition or {}).get("fields") or [])
        if mismatched:
            return None, (
                "shape_mismatch",
                f"'{secret.get('name')}' is not the shape this agent asked "
                f"for. It disagrees on: {', '.join(mismatched)}. Create a "
                f"credential from the shape it declares, or grant one that "
                f"matches.", 409)

        # A connected account is signed by one provider and consented for
        # named scopes. Neither is in the fields.
        have_provider = str(((definition or {}).get("oauth") or {}).get("provider") or "")
        have_scopes = {str(s) for s in ((definition or {}).get("oauth") or {}).get("scopes") or []}
        if wanted_provider != have_provider:
            return None, (
                "provider_mismatch",
                f"'{secret.get('name')}' is a {have_provider or 'typed-in'} "
                f"credential; this slot asks for {wanted_provider or 'a typed-in one'}.",
                409)
        missing = sorted(wanted_scopes - have_scopes)
        if missing:
            return None, (
                "scopes_missing",
                f"'{secret.get('name')}' was consented without scopes this "
                f"agent declared: {', '.join(missing)}. Connect the account "
                f"from this agent's page instead.", 409)

        doc = self.secret_grant_store.create(
            org_id, agent_id, resource_id, secret_ref,
            created_by=str(user.get("email") or ""),
        )
        AuditStore().append(
            "agent.secret_granted", user,
            resource_refs=[agent_id, secret_ref],
            details={"grant_id": doc["_id"], "resource_id": resource_id},
        )
        return AgentSecretGrantStore.to_public(doc), None

    def secretgrant(self, data, user):
        """Hand one saved credential to one of an agent's slots."""
        payload = self._payload(data)
        org_id = self._org(user)
        agent_id = str(payload.get("agent_id") or "")
        resource_id = str(payload.get("resource_id") or "")
        secret_ref = str(payload.get("secret_ref") or "")

        agent = self.store.get_in(org_id, agent_id)
        if agent is None:
            return self._fail(data, "not_found", "Agent is not installed.", 404)

        # Visible to the CALLER: granting is passing on access somebody
        # already has, never reaching past their own.
        secret = SecretStore().visible_doc(user, secret_ref)
        if secret is None:
            return self._fail(data, "not_found", "Secret not found.", 404)

        grant, refusal = self._grant_one(
            user, org_id, agent, resource_id, secret, secret_ref)
        if refusal:
            code, message, status = refusal
            return self._fail(data, code, message, status)
        return self._respond(data, {"grant": grant})

    # ------------------------------------------------------------------
    def _lend_targets(self, user, org_id: str, secret):
        """Every installed agent, other than the credential's own, with a
        slot for the same provider: the ones this credential could go
        to now, and the ones it could not with the reason why.

        Only same-provider slots are considered at all. A slot for
        another provider is not a near miss; it is a different account."""
        definition = self.definitions.get_in(
            org_id, str(secret.get("definition_ref") or "")) or {}
        oauth = definition.get("oauth") or {}
        provider = str(oauth.get("provider") or "")
        have_scopes = {str(s) for s in oauth.get("scopes") or []}
        home = self._family(secret.get("definition_ref"))
        # The raw document, as visible_doc returns it: the ref is its id.
        secret_ref = str(secret.get("resource_ref") or secret.get("_id") or "")
        secrets = SecretStore()

        eligible, skipped = [], []
        if not provider:
            return eligible, skipped     # a typed-in credential lends by hand

        for agent in self.store.list(org_id):
            if agent.get("status") != self.store.STATUS_INSTALLED:
                continue
            agent_id = str(agent["_id"])
            name = str(agent.get("qualified_id") or agent_id)
            granted = {
                doc["resource_id"]: doc
                for doc in self.secret_grant_store.for_agent(org_id, agent_id)
            }
            for resource in (agent.get("manifest") or {}).get(
                    "resources", {}).get("secrets") or []:
                resource_id = str(resource.get("id") or "")
                own_ref = self._own_ref(agent, resource_id)
                _, wanted_provider, wanted_scopes, own_id = self._slot_shape(
                    org_id, own_ref, resource)
                if wanted_provider != provider:
                    continue
                entry = {"agent_id": agent_id, "name": name,
                         "resource_id": resource_id,
                         "label": str(resource.get("label") or resource_id)}
                if self._family(own_ref) == home:
                    continue             # the slot it was made under
                grant = granted.get(resource_id)
                if grant is not None:
                    if str(grant.get("secret_ref") or "") == secret_ref:
                        skipped.append({**entry, "reason": "already lent this account"})
                    else:
                        skipped.append({**entry, "reason": "already using another account"})
                    continue
                # A credential saved under the slot's own definition
                # reaches the agent with no grant; the definition's slug
                # is the resource_id such credentials are stored under.
                if own_id and secrets.list_visible(user, resource_id=own_id):
                    skipped.append({**entry, "reason": "has its own account"})
                    continue
                missing = sorted(wanted_scopes - have_scopes)
                if missing:
                    skipped.append({**entry, "reason": (
                        "needs scopes this account was not consented for: "
                        + ", ".join(missing))})
                    continue
                eligible.append(entry)
        eligible.sort(key=lambda e: e["name"])
        skipped.sort(key=lambda e: e["name"])
        return eligible, skipped

    def secretlendable(self, data, user):
        """Which other installed agents one connected account could be
        lent to, and which could not, with the reason."""
        secret_ref = str(self._payload(data).get("secret_ref") or "")
        org_id = self._org(user)
        secret = SecretStore().visible_doc(user, secret_ref)
        if secret is None:
            return self._fail(data, "not_found", "Secret not found.", 404)
        eligible, skipped = self._lend_targets(user, org_id, secret)
        return self._respond(data, {"eligible": eligible, "skipped": skipped})

    def secretlendmany(self, data, user):
        """One connected account to several agents in one go: the single
        grant, repeated, in the order given. Stops at the first refusal
        and says what was done before it — the grants already written
        stand, because each is a decision that holds on its own."""
        payload = self._payload(data)
        org_id = self._org(user)
        secret_ref = str(payload.get("secret_ref") or "")
        targets = payload.get("agents")
        if not isinstance(targets, list) or not targets:
            return self._fail(data, "invalid", "Name at least one agent to lend to.", 400)
        if len(targets) > 50:
            return self._fail(data, "invalid", "At most fifty agents in one go.", 400)

        secret = SecretStore().visible_doc(user, secret_ref)
        if secret is None:
            return self._fail(data, "not_found", "Secret not found.", 404)

        granted, failed = [], None
        for target in targets:
            agent_id = str((target or {}).get("agent_id") or "")
            resource_id = str((target or {}).get("resource_id") or "")
            agent = self.store.get_in(org_id, agent_id)
            if agent is None:
                failed = {"agent_id": agent_id, "resource_id": resource_id,
                          "error": "Agent is not installed."}
                break
            grant, refusal = self._grant_one(
                user, org_id, agent, resource_id, secret, secret_ref)
            if refusal:
                failed = {"agent_id": agent_id, "resource_id": resource_id,
                          "error": refusal[1]}
                break
            granted.append({**grant, "name": str(agent.get("qualified_id") or agent_id)})

        result = {"granted": granted}
        if failed:
            result["failed"] = failed
        return self._respond(data, result)

    def secretrevoke(self, data, user):
        """Take it back. The agent keeps its declaration and its own
        definition; what it loses is the credential."""
        payload = self._payload(data)
        grant_id = str(payload.get("grant_id") or "")
        if not self.secret_grant_store.delete(self._org(user), grant_id):
            return self._fail(data, "not_found", "Grant not found.", 404)
        AuditStore().append(
            "agent.secret_revoked", user, resource_refs=[grant_id],
            details={"agent_ref": str(payload.get("agent_id") or "")},
        )
        return self._respond(data, {"revoked": True})
