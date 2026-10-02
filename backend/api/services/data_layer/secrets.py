"""Credentials — the controller. Values never read back through the
API; the only way out is ``use``, for a delegated runtime. The store
lives in database/stores/data/secrets.py."""

from __future__ import annotations


from api.services.data_layer.base import ResourceController
from api.services.oauth import OauthError, OauthFlow
from database.crypto import SecretCipherError
from database.stores.agents import AgentManifestStore, AgentSecretGrantStore
from database.stores.data.definitions import DefinitionStore
from database.stores.data.secrets import SecretStore
from database.stores.audit import AuditStore
from database.stores.chats import ChatStore
from database.stores.iam import UserStore



class SecretController(ResourceController):
    """Credentials. Strictest domain: values never read back, ever.
    Create/update are definition-driven — the caller sends a flat ``fields``
    dict and the definition decides what gets encrypted."""

    STORE = SecretStore
    ESCAPE_ACTION = "secrets:secret:set_owner_any"

    def __init__(self):
        super().__init__()
        self.definitions = DefinitionStore()

    def create(self, data: dict, user: dict):
        payload = self._payload(data)
        # `definition_id` is absent here, and only here: on create it is
        # an INPUT — the slug a caller names instead of an exact version
        # — and what gets STORED is still derived from the definition
        # that resolves. Update keeps it protected: a secret cannot be
        # re-pointed at a different definition after the fact.
        refusal = self._protected(
            payload, "org_id", "type", "resource_id", "definition_version")
        if refusal:
            return refusal

        org_id = str(user.get("org_id") or "")
        definition_ref = str(payload.get("definition_ref") or "").strip()
        definition_id = str(payload.get("definition_id") or "").strip().lower()
        if definition_ref and definition_id:
            # Two different answers to "which definition" is a
            # contradiction, not a preference — say so rather than
            # quietly honouring one of them.
            return {
                "error": "Name definition_ref or definition_id, not both.",
            }, 400
        if definition_ref:
            # An exact version: what an agent's approval recorded, and
            # what a secret must be validated against to stay comparable
            # to the manifest that asked for it.
            definition = self.definitions.get_in(org_id, definition_ref)
        elif definition_id:
            # By family or slug — the current version, for a caller that
            # knows the KIND of credential and not which version of it.
            definition = self.definitions.latest(org_id, definition_id)
        else:
            return {
                "error": "definition_ref or definition_id is required.",
            }, 400
        if definition is None:
            return {"error": "Secret definition version not found."}, 404

        raw_owner = payload.get("owner")
        if raw_owner is None:
            raw_owner = self._default_owner(user)
        owner, refusal = self._owner_or_refusal(user, raw_owner)
        if refusal:
            return refusal

        try:
            resource = self.store.create(
                user,
                definition=definition,
                name=payload.get("name"),
                owner=owner,
                fields=payload.get("fields"),
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400

        self.logger.info(
            f"{user.get('email')} created secret {resource['resource_ref']} "
            f"({resource['resource_id']} v{resource['definition_version']})"
        )
        return {"resource": resource}, 200

    def update(self, data: dict, user: dict):
        payload = self._payload(data)
        refusal = self._protected(
            payload, "org_id", "type", "resource_id", "definition_ref",
            "definition_version", "definition_id")
        if refusal:
            return refusal
        ref = str(payload.get("resource_ref") or "")

        doc = self.store.visible_doc(user, ref)
        if doc is None:
            return self._not_found()

        refusal = self._edit_refusal(user, doc)
        if refusal:
            return refusal

        # The RECORDED version — a later definition version never breaks an
        # existing instance's updates. ``migrate: true`` is the one
        # deliberate exception: the caller asks to move onto the
        # family's CURRENT version, carrying stored fields over and
        # validating the whole against the new shape.
        org_id = str(user.get("org_id") or "")
        recorded = str(doc.get("definition_ref") or "")
        latest = (
            self.definitions.latest(org_id, str(doc.get("resource_id") or ""))
            if payload.get("migrate") else None
        )
        # Asking to migrate a secret already on the latest version is not
        # an error, it is a no-op: fall through to the ordinary update.
        migrating = latest is not None and latest["_id"] != recorded
        if migrating:
            definition = latest
        else:
            definition = self.definitions.get_in(org_id, recorded)
        if definition is None:
            return {"error": "This secret's definition version is missing."}, 500

        owner = None
        if payload.get("owner") is not None:
            owner, refusal = self._owner_or_refusal(user, payload.get("owner"))
            if refusal:
                return refusal

        # UI rule: an empty encrypted field means "keep the current value".
        fields = payload.get("fields")
        if isinstance(fields, dict):
            values_fields = {
                f["name"] for f in definition.get("fields") or []
                if f["storage"] == "values"
            }
            fields = {
                key: value for key, value in fields.items()
                if not (key in values_fields and value == "")
            }

        try:
            if migrating:
                resource = self.store.migrate(doc, definition, fields)
                if payload.get("name") is not None or owner is not None:
                    fresh = self.store.visible_doc(user, ref)
                    resource = self.store.update(
                        fresh, definition=definition,
                        name=payload.get("name"), owner=owner)
            else:
                resource = self.store.update(
                    doc,
                    definition=definition,
                    name=payload.get("name"),
                    owner=owner,
                    fields=fields,
                )
        except ValueError as exc:
            return {"error": str(exc)}, 400

        self.logger.info(
            f"{user.get('email')} "
            + (f"migrated secret {ref} to v{definition['version']}"
               if migrating else f"updated secret {ref}"))
        return {"resource": resource}, 200

    def list(self, data: dict, user: dict):
        """The visible secrets, each with the agents allowed to use it.

        A credential's own page is the only place that can answer "what
        can read this". The agent side lists what one agent may use; the
        question people actually ask about a secret points the other
        way."""
        message, status = super().list(data, user)
        if status != 200:
            return message, status

        org_id = str(user.get("org_id") or "")
        grants = AgentSecretGrantStore()
        # qualified_ids is the readable-name index, name -> ref; a grant
        # is written against the ref, so it is read the other way here.
        names = {
            ref: name
            for name, ref in AgentManifestStore().qualified_ids(org_id).items()
        }
        for resource in message.get("resources") or []:
            used_by = grants.for_secret(org_id, resource.get("resource_ref", ""))
            resource["used_by_agents"] = [
                {"agent_ref": doc["agent_ref"],
                 "name": names.get(doc["agent_ref"], doc["agent_ref"]),
                 "resource_id": doc.get("resource_id", "")}
                for doc in used_by
            ]
            # A secret saved under an agent's own slot reaches that agent
            # with no grant row — named here so a delete dialog can warn
            # that the agent is left without a credential.
            family = str(resource.get("resource_id") or "")
            home_ref = family.split("__", 1)[0] if "__" in family else ""
            if home_ref.startswith("agt_") and home_ref in names:
                resource["home_agent"] = {
                    "agent_ref": home_ref, "name": names[home_ref],
                }
            if family.startswith("site__"):
                # A site login is used by consent, not by grant: each
                # pair on the row is one agent on one site.
                consents = str((resource.get("keys") or {}).get("consents") or "")
                for pair in sorted(p.strip() for p in consents.split(",") if p.strip()):
                    agent_ref, _, site = pair.partition("@")
                    resource["used_by_agents"].append({
                        "agent_ref": agent_ref,
                        "name": names.get(agent_ref, agent_ref),
                        "resource_id": "", "site": site})
        return message, status

    def delete(self, data: dict, user: dict):
        """The generic delete — refused while any agent holds a grant on
        this secret, and then the family's unused versions are pruned,
        because this may have been the last thing standing on one.

        The refusal is the point: a grant is a decision somebody made,
        and deleting the secret out from under it would break that
        agent silently. Taking the grants back first is explicit, one
        click each, and leaves nothing to discover in a failing chat."""
        org_id = str(user.get("org_id") or "")
        ref = str(self._payload(data).get("resource_ref") or "")
        doc = self.store.visible_doc(user, ref)

        granted_to = AgentSecretGrantStore().for_secret(org_id, ref)
        if granted_to:
            names = AgentManifestStore().qualified_ids(org_id)
            readable = {ref_: name for name, ref_ in names.items()}
            holders = sorted(
                readable.get(grant["agent_ref"], grant["agent_ref"])
                for grant in granted_to
            )
            return {
                "error": f"'{(doc or {}).get('name') or 'This secret'}' is "
                         f"granted to: {', '.join(holders)}. Take it back "
                         f"from them first — deleting it now would break "
                         f"them without a word.",
            }, 409

        message, status = super().delete(data, user)
        if status == 200 and doc is not None:
            self.definitions.prune_unused(
                org_id, str(doc.get("resource_id") or ""))
        return message, status

    def set_default(self, data: dict, user: dict):
        """Mark one credential as the caller's default for its family.

        A personal preference, never a value: it names which visible
        record answers when a chat has not chosen one itself. An empty
        resource_ref clears it."""
        if user.get("principal_type") == "runtime":
            return {"error": "A person sets their defaults, not a chat."}, 403
        user_id = str(user.get("user_id") or "")
        if not user_id:
            return {"error": "unauthorized"}, 401

        payload = self._payload(data)
        family = str(payload.get("resource_id") or "").strip()
        ref = str(payload.get("resource_ref") or "").strip()
        if not family:
            return {"error": "resource_id (the credential family) is required."}, 400

        if not ref:
            UserStore().set_secret_default(user_id, family, None)
            return {"cleared": True}, 200

        doc = self.store.visible_doc(user, ref)
        if doc is None or doc.get("resource_id") != family:
            return {"error": "That secret is not visible to you, or does "
                             "not belong to that credential family."}, 404

        UserStore().set_secret_default(user_id, family, ref)
        self.logger.info(f"{user.get('email')} set default for {family}")
        return {"default": ref}, 200

    async def use(self, data: dict, user: dict):
        """Return plaintext only to an authenticated delegated runtime.

        Callers name either an exact instance (``resource_ref``) or a
        category (``resource_id``) — the manifest's canonical
        ``agent__resource`` id. A category resolves through the chat's
        explicit binding (``config.bindings``) first, else the sole
        visible instance; anything ambiguous is an error, never a guess."""
        if user.get("principal_type") != "runtime":
            return {"error": "Only the AI runtime may use secret values."}, 403

        payload = self._payload(data)
        ref = str(payload.get("resource_ref") or "")
        category = str(payload.get("resource_id") or "")

        if not ref:
            if not category:
                return {"error": "resource_ref or resource_id is required."}, 400
            ref, refusal = self._resolve_binding(user, category)
            if refusal:
                return refusal
        elif category and not self._eligible(user, category, ref):
            # An agent naming one of several (list_secrets) may name only
            # what its slot could have been handed: a row under its own
            # family, or the one granted to it. Anything else it can see
            # is somebody's credential for something else.
            return {"error": f"Secret '{ref}' is not usable for "
                             f"'{category}'."}, 403

        try:
            values = await self._values_for_agent(user, ref)
        except ValueError as exc:
            return {"error": str(exc)}, 404
        except OauthError as exc:
            # The grant behind a connected account is gone; only the
            # person can bring it back, and the sentence says how.
            self.logger.warning(f"{ref}: {exc}")
            return {"error": str(exc)}, 409
        except SecretCipherError as exc:
            # The secret is there and the caller may read it; the key it
            # was sealed with is not configured. That is a deployment
            # fact with an obvious remedy, and reporting it as a 500
            # ("internal error") hides the one sentence that fixes it.
            self.logger.error(f"{ref} cannot be decrypted: {exc}")
            return {"error": str(exc)}, 409

        doc = self.store.visible_doc(user, ref) or {}
        behind = self._behind(user, category, doc, values)
        if behind:
            return {"error": behind}, 409

        AuditStore().append(
            "secret.use", user,
            chat_id=str(user.get("chat_id") or ""),
            resource_refs=[ref],
        )
        # Keys travel beside the values: a definition routes only what
        # needs encryption into values, and the plain fields (a hostname,
        # a provider name) are part of the same credential — an agent
        # that gets the password but not the host has half a secret.
        return {"keys": dict(doc.get("keys") or {}), "values": values}, 200

    def _behind(self, user: dict, category: str, doc: dict, values: dict) -> str:
        """Why a credential of the agent's own family cannot be used as
        it is, or ''. An agent's update may change the shape it reads;
        a credential saved before that follows by itself where it can
        (the install's move_forward), and where it cannot — the new
        shape requires something it does not have — the agent is not
        handed half of what it now expects. The sentence says where
        the person completes it."""
        if not category:
            return ""
        org_id = str(user.get("org_id") or "")
        expected = self.definitions.latest(org_id, category)
        if expected is None:
            return ""
        if doc.get("resource_id") != category:
            # Lent from another family. It was lent because its shape
            # was exactly the one this agent declared, and the agent's
            # update may have declared another since.
            return self._no_longer_fits(org_id, expected, doc)
        if expected["_id"] == doc.get("definition_ref"):
            return ""
        held = {**(doc.get("keys") or {}), **(values or {})}
        missing = [
            field["label"] for field in expected.get("fields") or []
            if field.get("required")
            and not DefinitionStore._provided(field, held.get(field["name"]))
        ]
        if not missing:
            return ""
        name = doc.get("name") or "This credential"
        return (f"'{name}' was saved for an older version of this agent, "
                f"which now also needs: {', '.join(missing)}. Open it under "
                f"Secrets and save it to bring it up to date.")

    def _no_longer_fits(self, org_id: str, expected: dict, doc: dict) -> str:
        """Why a lent credential is not this slot's any more, or ''."""
        lent = self.definitions.get_in(
            org_id, str(doc.get("definition_ref") or "")) or {}
        if not self.fits(expected, lent):
            name = doc.get("name") or "The credential"
            return (f"'{name}' was lent to this agent when it had the shape "
                    f"the agent asked for. The agent was updated and asks "
                    f"for a different one now: take it back on the agent's "
                    f"page and give the agent a credential of its own.")
        return ""

    @staticmethod
    def fits(slot: dict, lent: dict) -> bool:
        """Whether a credential of one definition is what a slot of
        another expects: the same fields, and for a connected account
        the same provider."""
        if DefinitionStore.shape_mismatch(
                slot.get("fields") or [], lent.get("fields") or []):
            return False
        return (str((slot.get("oauth") or {}).get("provider") or "")
                == str((lent.get("oauth") or {}).get("provider") or ""))

    async def _values_for_agent(self, user: dict, ref: str) -> dict:
        """The decrypted values — for a connected account, with an
        access token that is still good and without the refresh token,
        which stays the backend's."""
        doc = self.store.visible_doc(user, ref)
        if doc is None:
            raise ValueError("Secret not found.")
        definition = self.definitions.get_in(
            str(user.get("org_id") or ""), str(doc.get("definition_ref") or ""))
        if definition is not None and definition.get("oauth"):
            return await OauthFlow().fresh(user, doc, definition)
        return self.store.use(user, ref)

    def instances(self, data: dict, user: dict):
        """Every instance one slot may use, plain half only — for an
        agent that offers the person a choice among their credentials
        (a second mailbox, another site) or matches an account the
        model named. Runtime-only, like ``use``; a value never leaves
        here, and the person's visibility bounds the list."""
        if user.get("principal_type") != "runtime":
            return {"error": "Only the AI runtime may list a slot's "
                             "credentials."}, 403
        category = str(self._payload(data).get("resource_id") or "")
        if not category:
            return {"error": "resource_id is required."}, 400
        bound = self._chat_binding(user, category)
        preferred = self._user_default(user, category)
        rows = list(self.store.list_visible(user, resource_id=category))
        granted = AgentSecretGrantStore().resolve(
            str(user.get("org_id") or ""), category)
        if granted and all(r.get("resource_ref") != granted for r in rows):
            doc = self.store.visible_doc(user, granted)
            if doc is not None:
                rows.append(self.store.to_public(doc, with_values=False))
        return {"instances": [
            {"resource_ref": str(row.get("resource_ref") or ""),
             "name": str(row.get("name") or ""),
             "keys": dict(row.get("keys") or {}),
             "definition_version": row.get("definition_version"),
             "is_default": row.get("resource_ref") == preferred,
             "is_bound": row.get("resource_ref") == bound}
            for row in rows
        ]}, 200

    def _eligible(self, user: dict, category: str, ref: str) -> bool:
        """Whether one instance is a slot's to use: under the slot's own
        family, or granted to it."""
        doc = self.store.visible_doc(user, ref)
        if doc is None:
            return False
        if doc.get("resource_id") == category:
            return True
        return AgentSecretGrantStore().resolve(
            str(user.get("org_id") or ""), category) == ref

    def _resolve_binding(self, user: dict, category: str):
        """category → the instance that should answer, or a refusal.

        In order: the chat's own choice, then a credential granted to the
        agent asking, then the person's default, then the only instance
        visible. Each step is a narrower authority than the one before
        it, and the ambiguity error at the end is the state the default
        exists to remove."""
        bound = self._chat_binding(user, category)
        if bound:
            doc = self.store.visible_doc(user, bound)
            if doc is None or doc.get("resource_id") != category:
                return "", (
                    {"error": f"The chat's binding for '{category}' names a "
                              f"secret that is not usable."}, 404,
                )
            return bound, None

        # An agent's category is `<agent_ref>__<resource_id>`, so it names
        # exactly one approval — which is how a grant written against that
        # agent can be found from nothing but the string it asks with.
        # This is the step that lets a credential filled in under somebody
        # else's definition reach the agent at all, and the only one that
        # does: without a row here, an agent sees what its own definition
        # holds and nothing more.
        granted = AgentSecretGrantStore().resolve(
            str(user.get("org_id") or ""), category)
        if granted:
            # Checked, not trusted, for the same reason the default below
            # is: a grant written last month may name a secret since
            # deleted, or one this person can no longer see.
            doc = self.store.visible_doc(user, granted)
            if doc is not None:
                return granted, None

        preferred = self._user_default(user, category)
        if preferred:
            # Checked, not trusted: a default set months ago may name a
            # secret since deleted or unshared. Then it simply does not
            # apply, rather than failing a chat over stale housekeeping.
            doc = self.store.visible_doc(user, preferred)
            if doc is not None and doc.get("resource_id") == category:
                return preferred, None

        instances = self.store.list_visible(user, resource_id=category)
        if len(instances) == 1:
            return instances[0]["resource_ref"], None
        if not instances:
            return "", (
                {"error": f"No secret instance is available for "
                          f"'{category}'."}, 404,
            )
        return "", (
            {"error": f"Multiple secret instances exist for '{category}' — "
                      f"mark one as your default on the agent's page, or "
                      f"bind one in the chat's config.bindings."}, 409,
        )

    @staticmethod
    def _user_default(user: dict, category: str) -> str:
        user_id = str(user.get("user_id") or "")
        return UserStore().secret_default(user_id, category) if user_id else ""

    @staticmethod
    def _chat_binding(user: dict, category: str) -> str:
        """The delegated chat's explicit binding for this category.

        The one place this domain reads the chat domain: a chat may
        name which credential answers for a category, and that choice
        outranks every default below it."""
        chat_id = str(user.get("chat_id") or "")
        if not chat_id:
            return ""
        chat = ChatStore().for_identity({
            "chat_id": chat_id,
            "org_id": str(user.get("org_id") or ""),
            "user_id": str(user.get("user_id") or ""),
        })
        bindings = ((chat or {}).get("config") or {}).get("bindings") or {}
        return str(bindings.get(category) or "")
