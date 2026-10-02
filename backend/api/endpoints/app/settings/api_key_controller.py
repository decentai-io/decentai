"""Settings:ApiKey — a person's own keys for scripts and integrations.

A key acts as its owner: same user, same organization, same policy
chain on every request. It is shown once, at creation; afterwards the
page sees a name, a prefix and the dates. Revoking is the only edit.

Keys are made and revoked from a browser session only. A key that
could mint keys would make a leak permanent; a key that could revoke
keys would let a leak lock its owner out of their own scripts.
"""

from api.endpoints.app.ai.base import AIController
from database.stores import ApiKeyStore
from server.custom_logging import CustomLoggerFactory


class ApiKeyController(AIController):
    Name = "ApiKey"

    def __init__(self):
        self.store = ApiKeyStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    def _from_browser(self, data, user):
        if str(user.get("token_type") or "").upper() == "WEB":
            return None
        return self._fail(
            data, "forbidden",
            "API keys are managed from a signed-in browser session, not "
            "with a key.", 403)

    def list(self, data, user):
        return self._respond(data, {
            "keys": self.store.list_for(user),
            "limit": ApiKeyStore.MAX_PER_USER,
        })

    def create(self, data, user):
        refusal = self._from_browser(data, user)
        if refusal is not None:
            return refusal
        try:
            made = self.store.create(user, self._payload(data).get("name"))
        except ValueError as exc:
            return self._fail(data, "invalid_key", str(exc))
        self.logger.info(f"{user.get('email')} created API key "
                         f"{made['key_id']} ({made['name']})")
        return self._respond(data, {"key": made})

    def revoke(self, data, user):
        refusal = self._from_browser(data, user)
        if refusal is not None:
            return refusal
        key_id = str(self._payload(data).get("key_id") or "")
        if not self.store.revoke(user, key_id):
            return self._fail(data, "not_found", "Key not found.", 404)
        self.logger.info(f"{user.get('email')} revoked API key {key_id}")
        return self._respond(data, {"revoked": True})
