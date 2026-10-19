"""Settings:Llm — the providers an organization's chats may think with.

Its own module with its own collection. A connection used to be a secret
on a seeded definition, which meant naming a model dragged in the whole
definition machinery — versions, shapes, instance counts — for a form
the platform has always known by heart. What survives from that design
is the part that mattered: the key is encrypted the same way and never
comes back.

A connection is a provider and its key. The model is chosen where it is
used — by a chat, by agent routing, by speech — from the ones that
provider serves; a connection only names the one it starts with.

One connection is the DEFAULT — the organization's answer when nothing
narrower chose. A person's preference and a chat's own config may each
name a different connection and model; the default is only where
resolution ends, never a lock.
"""

from __future__ import annotations

from api.services.llm_probe import ProviderProbe
from contracts.llm_providers import LlmProviders
from database.stores.settings.llm import LlmConnectionStore
from server.custom_logging import CustomLoggerFactory


class LlmController:
    Name = "Llm"

    def __init__(self):
        self.store = LlmConnectionStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict):
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    @staticmethod
    def _org(user: dict) -> str:
        return str(user.get("org_id") or "")

    @staticmethod
    def _owner_reach_refusal(user: dict, owner):
        """Shared with every record type that shares — see
        server/governance/sharing.py. The store checks the ids EXIST;
        this checks the caller may reach them."""
        from server.governance import INFRASTRUCTURE, Sharing

        return Sharing(INFRASTRUCTURE, "connection").reach_refusal(
            user, owner)

    def list(self, data: dict, user: dict):
        """Every connection this caller can see, default first. Keys
        metadata only — the api key is write-only, here as everywhere.

        ``manage: true`` is the page connections are kept on asking: a
        holder of the manage-any grant is listed every connection of
        the organization there, a colleague's unshared one included,
        and anybody else what they would have been listed anyway."""
        every = (self._payload(data).get("manage") is True
                 and self._may_manage_any(user))
        return {"connections": self.store.list(user, every=every)}, 200

    def providers(self, data: dict, user: dict):
        """The providers a connection may name, for the form that adds
        one: each with the protocol it speaks and the address the form
        starts with. The same catalog the store validates against and
        the runtime builds connectors from, so the page cannot offer a
        provider the platform would then refuse.

        Asked about one ``provider``, it answers with the models that
        provider is known to serve instead, each with what it is for and
        what it can do — and only those of one ``kind`` (chat, embedding,
        transcription) when one is named. Fetched when a provider is
        chosen, so the whole list is not sent to draw a dropdown."""
        payload = self._payload(data)
        provider = payload.get("provider")
        if provider:
            kind = str(payload.get("kind") or "").strip().lower()
            return {"models": LlmProviders.models(provider, kind)}, 200
        return {"providers": LlmProviders.all()}, 200

    def models(self, data: dict, user: dict):
        """The models ONE connection offers, of one ``kind`` (chat when
        none is named): what a chat's picker lists under it, and what
        routing and speech choose among.

        From the catalog where it knows the connection's provider. Where
        it does not — a server of the person's own, a gateway — the
        server is asked for its own list with the connection's key
        (``live``), since nothing else could know. The model the
        connection starts with is always among a chat's, listed or not."""
        payload = self._payload(data)
        connection = self.store.visible(
            user, str(payload.get("connection_id") or ""))
        if connection is None:
            return {"error": "Connection not found."}, 404
        kind = str(payload.get("kind") or "chat").strip().lower()
        provider = connection.get("provider", "")
        # A provider that is called by deployment offers no list: the
        # catalog's models are not what this customer deployed.
        by_deployment = LlmProviders.by_deployment(provider)
        models = [] if by_deployment else LlmProviders.models(provider, kind)
        live = False
        if not by_deployment and not LlmProviders.models(provider):
            resolved = self.store.use(user, connection["_id"]) or {}
            listed = ProviderProbe(
                provider, connection.get("endpoint"),
                (resolved.get("values") or {}).get("api_key"),
                connection.get("model")).models()
            # A server says which models it has, not what each is for.
            models = [{"id": name, "name": name, "kind": kind} for name in listed]
            live = bool(listed)
        starting = str(connection.get("model") or "")
        if kind == "chat" and starting and all(
                model["id"] != starting for model in models):
            models.insert(0, {"id": starting, "name": starting, "kind": "chat"})
        return {"models": models, "live": live}, 200

    def _checked(self, payload: dict, provider, endpoint, api_key, model):
        """(the provider's answer, a refusal or None). Asked only when
        the form asks (``check``): a key the provider refuses, or an
        address nobody answers at, is not saved — the person is told
        which, and may save all the same by not asking."""
        if not payload.get("check"):
            return None, None
        answer = ProviderProbe(provider, endpoint, api_key, model).check()
        if answer["outcome"] in (ProviderProbe.REFUSED, ProviderProbe.UNREACHABLE):
            return answer, ({"error": answer["reason"], "check": answer}, 400)
        return answer, None

    def create(self, data: dict, user: dict):
        payload = self._payload(data)
        refusal = self._owner_reach_refusal(user, payload.get("owner"))
        if refusal:
            return refusal
        checked, refusal = self._checked(
            payload, payload.get("provider"), payload.get("endpoint"),
            payload.get("api_key"), payload.get("model"))
        if refusal:
            return refusal
        try:
            connection = self.store.create(
                self._org(user),
                name=payload.get("name"),
                fields={
                    "provider": payload.get("provider"),
                    "model": payload.get("model"),
                    "endpoint": payload.get("endpoint"),
                },
                api_key=payload.get("api_key"),
                created_by=str(user.get("user_id") or ""),
                owner=payload.get("owner"),
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400

        self.logger.info(
            f"{user.get('email')} added LLM connection "
            f"{connection['resource_ref']} ({connection['keys']['provider']})"
        )
        return self._answer(connection, checked), 200

    @staticmethod
    def _answer(connection: dict, checked) -> dict:
        answer = {"connection": connection}
        if checked is not None:
            answer["check"] = checked
        return answer

    #: The administrator's escape from the creator-only rule.
    MANAGE_ANY = "settings:llm:manage_any"

    def _may_manage_any(self, user: dict) -> bool:
        from server.authentication.policy import PolicyEngine

        return PolicyEngine().is_allowed(user, self.MANAGE_ANY)

    def _edit_refusal(self, user: dict, connection_id: str):
        """Creator-only, the secret layer's rule: being shared a
        connection — even holding the update grant — is permission to
        USE it, never authority over it. Whoever typed the key in
        decides when it changes and when it goes — unless the caller
        holds the manage-any grant, the administrator's escape that
        keeps an organization from being locked out of a connection a
        colleague set up."""
        doc = self._reached(user, connection_id)
        if doc is None:
            return {"error": "Connection not found."}, 404
        if str(doc.get("created_by") or "") != str(user.get("user_id") or "") \
                and not self._may_manage_any(user):
            return {
                "error": "Only the person who added a connection can "
                         "change or delete it.",
            }, 403
        return None

    def _reached(self, user: dict, connection_id: str):
        """The connection an edit is about: one the caller can see, or,
        holding the manage-any grant, any of the organization's. A
        connection is private until its creator shares it, so an
        escape that reached only what was already shared left the
        organization locked out of exactly the one it was made for:
        a key a colleague set up and never shared."""
        doc = self.store.visible(user, connection_id)
        if doc is None and self._may_manage_any(user):
            doc = self.store.in_organization(self._org(user), connection_id)
        return doc

    def transfer(self, data: dict, user: dict):
        """Hand a connection to another member: the creator's act, or an
        escape-grant holder's."""
        from api.services.successor import successor_or_error

        payload = self._payload(data)
        connection_id = str(payload.get("connection_id") or "")
        refusal = self._edit_refusal(user, connection_id)
        if refusal:
            return refusal
        doc = self._reached(user, connection_id)
        successor, why = successor_or_error(
            self._org(user), payload.get("user_id"),
            excluding=str(doc.get("created_by") or ""))
        if successor is None:
            return {"error": why}, 400
        connection = self.store.transfer(self._org(user), connection_id, successor["_id"])
        self.logger.info(f"{user.get('email')} handed connection {connection_id} "
                         f"to {successor.get('email')}")
        return {"connection": connection}, 200

    def update(self, data: dict, user: dict):
        """Partial. A blank api_key keeps the stored one, so rotating the
        model never means retyping the key."""
        payload = self._payload(data)
        refusal = self._edit_refusal(
            user, str(payload.get("connection_id") or ""))
        if refusal:
            return refusal
        refusal = self._owner_reach_refusal(user, payload.get("owner"))
        if refusal:
            return refusal
        fields = {
            key: payload[key]
            for key in ("provider", "model", "endpoint")
            if key in payload
        }
        checked = None
        if payload.get("check"):
            # The key typed now, or the stored one where none was: what
            # is asked about is the connection as it would be saved.
            connection_id = str(payload.get("connection_id") or "")
            stored = self._reached(user, connection_id) or {}
            typed = str(payload.get("api_key") or "").strip()
            key = typed or ((self.store.use(user, connection_id) or {})
                            .get("values") or {}).get("api_key")
            checked, refusal = self._checked(
                payload, fields.get("provider", stored.get("provider")),
                fields.get("endpoint", stored.get("endpoint")), key,
                fields.get("model", stored.get("model")))
            if refusal:
                return refusal
        try:
            connection = self.store.update(
                self._org(user),
                str(payload.get("connection_id") or ""),
                name=payload.get("name"),
                fields=fields,
                api_key=payload.get("api_key"),
                owner=payload.get("owner"),
            )
        except ValueError as exc:
            return {"error": str(exc)}, 400
        if connection is None:
            return {"error": "Connection not found."}, 404

        self.logger.info(
            f"{user.get('email')} updated LLM connection "
            f"{connection['resource_ref']}"
        )
        return self._answer(connection, checked), 200

    def setdefault(self, data: dict, user: dict):
        """Re-point the org default — an act about the ORGANIZATION, not
        an edit of the record, so any holder of the grant may do it. But
        only among connections they can see: pointing everyone at a
        model you cannot even list would be defaulting blind."""
        chosen = self.store.visible(
            user, str(self._payload(data).get("connection_id") or ""))
        if chosen is None:
            return {"error": "Connection not found."}, 404
        try:
            connection = self.store.set_default(
                self._org(user),
                str(self._payload(data).get("connection_id") or ""))
        except ValueError as exc:
            return {"error": str(exc)}, 400
        if connection is None:
            return {"error": "Connection not found."}, 404

        self.logger.info(
            f"{user.get('email')} made {connection['resource_ref']} the "
            f"default LLM connection"
        )
        return {"connection": connection}, 200

    def use(self, data: dict, user: dict):
        """The key, decrypted — to a delegated runtime only, at the
        settings domain's own door.

        By id when the chat's llm block carried one; by the
        organization's default when none is named. Visibility follows
        the DELEGATING PERSON either way: a chat can only think with a
        model its person may see, defaults included."""
        if user.get("principal_type") != "runtime":
            return {"error": "Only the AI runtime may use the model "
                             "key."}, 403

        from database.crypto import SecretCipherError
        from database.stores import AuditStore

        ref = str(self._payload(data).get("connection_id") or "")
        if not ref:
            default = self.store.default(self._org(user))
            if default is None:
                return {"error": "No LLM connection is configured — add "
                                 "one under Settings."}, 404
            ref = str(default["_id"])

        try:
            resolved = self.store.use(user, ref)
        except SecretCipherError as exc:
            self.logger.error(f"{ref} cannot be decrypted: {exc}")
            return {"error": str(exc)}, 409
        if resolved is None:
            return {"error": "That LLM connection is not available."}, 404

        AuditStore().append(
            "llm.use", user,
            chat_id=str(user.get("chat_id") or ""),
            resource_refs=[ref],
        )
        return resolved, 200

    def delete(self, data: dict, user: dict):
        connection_id = str(self._payload(data).get("connection_id") or "")
        refusal = self._edit_refusal(user, connection_id)
        if refusal:
            return refusal
        if not self.store.delete(self._org(user), connection_id):
            return {"error": "Connection not found."}, 404

        self.logger.info(
            f"{user.get('email')} deleted LLM connection {connection_id}")
        return {"deleted": True}, 200
