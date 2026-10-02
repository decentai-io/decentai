"""The mind, as a record (docs/system/chat-session.md).

The runtime persists its assistant's whole state every beat and reads
it back on hydration; this door stores and serves that one document —
the chat's own, or a sub-assistant thread's under it. One writer,
replaced whole, no version dance: the runtime stamps its own schema
version inside. Resilience, never authority: a failed save costs the
runtime one beat, and nothing here decides anything.
"""

import json

from database.stores import ChatStore

from .base import AIController


class StateController(AIController):
    #: generous — a transcript folds and a trace compacts on the
    #: runtime's side well before this.
    STATE_MAX_BYTES = 512 * 1024

    def get(self, data, user):
        chat, refusal = self._runtime_chat(data, user, "read the state")
        if refusal is not None:
            return refusal
        thread, refusal = self._thread(data)
        if refusal is not None:
            return refusal
        return self._respond(data, {
            "state": ChatStore.state_of(chat, thread),
        })

    def save(self, data, user):
        chat, refusal = self._runtime_chat(data, user, "save the state")
        if refusal is not None:
            return refusal
        thread, refusal = self._thread(data)
        if refusal is not None:
            return refusal

        state = self._payload(data).get("state")
        if not isinstance(state, dict):
            return self._fail(
                data, "invalid_request", "state must be an object.")
        size = len(json.dumps(state, default=str).encode("utf-8"))
        if size > self.STATE_MAX_BYTES:
            return self._fail(
                data, "state_too_large",
                f"State is {size} bytes; the limit is "
                f"{self.STATE_MAX_BYTES}.")

        ChatStore().set_state(chat["chat_id"], state, thread)
        return self._respond(data, {"saved": True, "bytes": size})
