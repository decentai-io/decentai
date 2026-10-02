"""Chat storage: one document per verified function result.

The executor stores every successful invocation's result here (runtime-
only, chat-bound); rich message parts and later invocations reference it
by ``storage_ref`` — verified values live once, and the model never
retypes them. Reading belongs to the user (the frontend renders tables
and graphs from here), optionally narrowed by a dot-path.
"""

from database.stores import ChatStorageStore
from util import new_id, utc_now

from .base import AIController


class StorageController(AIController):
    def create(self, data, user):
        chat, refusal = self._runtime_chat(
            data, user, "create storage results")
        if refusal is not None:
            return refusal

        payload = self._payload(data)
        source = str(payload.get("source") or "")
        if not source:
            return self._fail(data, "invalid_request", "source is required.")

        document = {
            **self._identity(user, chat["chat_id"]),
            # The schema's field is storage_id; the API calls references
            # to it storage_ref (id vs ref, as in the data layer).
            "storage_id": f"stg_{new_id()}",
            "source": source,
            "data": payload.get("data"),
            "meta": payload.get("meta") or {},
            "created_at": utc_now(),
        }
        ChatStorageStore().insert(document)
        return self._respond(data, {"storage": self._public_result(document)})

    def get(self, data, user):
        payload = self._payload(data)
        scope = dict(self._owner(user))
        # A delegation reads only ITS chat's results (reference-input
        # resolution); users read across their chats.
        if user.get("principal_type") == "runtime":
            scope["chat_id"] = str(user.get("chat_id") or "")
        document = ChatStorageStore().get_scoped(
            str(payload.get("storage_ref") or ""), scope)
        if document is None:
            return self._fail(data, "not_found", "Storage result not found.", 404)

        path = str(payload.get("path") or "")
        if path:
            try:
                value = self._walk(document.get("data"), path)
            except (KeyError, IndexError, TypeError, ValueError):
                return self._fail(
                    data, "invalid_path",
                    f"Path '{path}' does not resolve in this result.", 404,
                )
            return self._respond(data, {
                "storage_ref": document["storage_id"], "path": path,
                "value": value,
            })
        return self._respond(data, {"storage": self._public_result(document)})

    # ------------------------------------------------------------------
    def _public_result(self, document):
        """A stored result as the API names it: the schema's storage_id
        is a storage_ref on the way out (id vs ref, as in the data
        layer)."""
        public = self._public(document)
        public["storage_ref"] = public.pop("storage_id")
        return public

    @staticmethod
    def _walk(value, path):
        """Dot-path lookup: "data", "rows.0.title"."""
        for part in path.split("."):
            if isinstance(value, dict):
                value = value[part]
            elif isinstance(value, list):
                value = value[int(part)]
            else:
                raise TypeError(part)
        return value
