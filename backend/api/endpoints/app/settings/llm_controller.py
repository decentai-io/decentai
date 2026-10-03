"""Settings:Llm — the models an organization's chats may think with.

Its own module with its own collection. A connection used to be a secret
on a seeded definition, which meant naming a model dragged in the whole
definition machinery — versions, shapes, instance counts — for a form
the platform has always known by heart. What survives from that design
is the part that mattered: the key is encrypted the same way and never
comes back.

One connection is the DEFAULT — the organization's answer when nothing
narrower chose. A person's preference and a chat's own config may each
name a different connection; the default is only where resolution ends,
never a lock.
"""

from __future__ import annotations

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
        metadata only — the api key is write-only, here as everywhere."""
        return {"connections": self.store.list(user)}, 200

    def providers(self, data: dict, user: dict):
        """The providers a connection may name, for the form that adds
        one: each with the protocol it speaks and the address the form
        starts with. The same catalog the store validates against and
        the runtime builds connectors from, so the page cannot offer a
        provider the platform would then refuse."""
        return {"providers": LlmProviders.all()}, 200

    def create(self, data: dict, user: dict):
        payload = self._payload(data)
        refusal = self._owner_reach_refusal(user, payload.get("owner"))
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
                    "reasoning_effort": payload.get("reasoning_effort"),
                    "purpose": payload.get("purpose") or "chat",
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
        return {"connection": connection}, 200

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
        doc = self.store.visible(user, connection_id)
        if doc is None:
            return {"error": "Connection not found."}, 404
        if str(doc.get("created_by") or "") != str(user.get("user_id") or "") \
                and not self._may_manage_any(user):
            return {
                "error": "Only the person who added a connection can "
                         "change or delete it.",
            }, 403
        return None

    def transfer(self, data: dict, user: dict):
        """Hand a connection to another member: the creator's act, or an
        escape-grant holder's."""
        from api.services.successor import successor_or_error

        payload = self._payload(data)
        connection_id = str(payload.get("connection_id") or "")
        refusal = self._edit_refusal(user, connection_id)
        if refusal:
            return refusal
        doc = self.store.visible(user, connection_id)
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
            for key in ("provider", "model", "endpoint", "reasoning_effort", "purpose")
            if key in payload
        }
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
        return {"connection": connection}, 200

    def setdefault(self, data: dict, user: dict):
        """Re-point the org default — an act about the ORGANIZATION, not
        an edit of the record, so any holder of the grant may do it. But
        only among connections they can see: pointing everyone at a
        model you cannot even list would be defaulting blind."""
        chosen = self.store.visible(
            user, str(self._payload(data).get("connection_id") or ""))
        if chosen is None:
            return {"error": "Connection not found."}, 404
        if chosen.get("purpose") != "chat":
            return {"error": "Only a chat model can be the default: no chat "
                             "thinks with an embedding or transcription "
                             "model."}, 400
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
