"""The mind, as a record (docs/system/chat-session.md).

The runtime persists its assistant's whole state every beat and reads
it back on hydration; this door stores and serves that one document —
the chat's own, or a sub-assistant thread's under it. One writer,
replaced whole, no version dance: the runtime stamps its own schema
version inside. Resilience, never authority: a failed save costs the
runtime one beat, and nothing here decides anything.

And one door for the person whose chat it is: ``transcript``, what the
assistant was told and what it decided, as the model was shown it —
its instructions, each thing that arrived, each action it chose. Read
from the same document, changed by nothing: a person looking at how
their assistant reasoned, in their own chat and nobody else's.
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

    # ------------------------------------------------------------------
    # The person's read
    # ------------------------------------------------------------------

    #: What one entry may carry of its text. The whole mind is bounded
    #: above; one result pasted into it should not fill a page.
    ENTRY_MAX_CHARS = 20_000

    def transcript(self, data, user):
        """What the assistant of the caller's own chat was told and
        what it decided, in the order the model was shown it. The chat
        must be theirs to open; a chat that is somebody else's is, to
        them, not there."""
        chat, refusal = self._chat_or_refusal(data, user)
        if refusal is not None:
            return refusal
        thread, refusal = self._thread(data)
        if refusal is not None:
            return refusal
        state = ChatStore.state_of(chat, thread) or {}
        entries = []
        for index, message in enumerate(state.get("messages") or []):
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            text = content if isinstance(content, str) else json.dumps(
                content, default=str)
            images = message.get("images")
            entries.append({
                "index": index,
                "role": str(message.get("role") or ""),
                "content": text[: self.ENTRY_MAX_CHARS],
                "cut": len(text) > self.ENTRY_MAX_CHARS,
                # A picture shown to the model is counted, not copied.
                "images": len(images) if isinstance(images, list) else 0,
            })
        return self._respond(data, {
            "entries": entries,
            # What was folded away to keep the mind small: the model is
            # shown this in place of the oldest part of the chat.
            "summary": str(state.get("summary") or ""),
            "beats": int(state.get("beats") or 0),
            "opened": [str(agent) for agent in state.get("opened") or []],
        })
