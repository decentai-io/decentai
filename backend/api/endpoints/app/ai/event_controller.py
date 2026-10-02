"""The chat's two event logs (docs/system/chat-session.md).

OUT — the narration: the runtime appends each replayable event before
emitting it; the backend allocates the per-chat sequence and keeps a
bounded tail; any client arriving late lists events past its last-seen
sequence and replays them in order. Resilience, never authority.

IN — the inbox: what the world told the assistant (a message, a
wakeup, a data change), recorded with its sequence BEFORE the runtime
absorbs it. The runtime's cursor is a bookmark into this sequence, so a
mind that died before a beat persisted finds the event again at the
next hydration — exactly once (docs/system/assistant.md).
"""

import json

from contracts.chat import event_error
from database.stores import ChatEventStore, ChatStore
from util import utc_now

from .base import AIController


class EventController(AIController):
    EVENT_MAX_BYTES = 16384
    EVENTS_KEPT = 200
    DEFAULT_LIMIT = 100
    #: Never truncates a legitimate replay: the tail kept per chat is
    #: EVENTS_KEPT, well under this.
    MAX_LIMIT = 500

    def append(self, data, user):
        chat, refusal = self._runtime_chat(data, user, "append events")
        if refusal is not None:
            return refusal

        payload = self._payload(data)
        event = payload.get("event")
        if not isinstance(event, dict) or not isinstance(
            event.get("event"), str
        ) or not event["event"]:
            return self._fail(
                data, "invalid_request",
                "event must be an object with an event name.",
            )
        size = len(json.dumps(event, default=str).encode("utf-8"))
        if size > self.EVENT_MAX_BYTES:
            return self._fail(
                data, "event_too_large",
                f"Event is {size} bytes; the limit is "
                f"{self.EVENT_MAX_BYTES}.",
            )
        # The vocabulary is the contract's (contracts/chat.py): an event
        # that does not fit is refused, not kept for a page to guess at.
        # The runtime logs the refusal; the live frame still reaches an
        # open chat, it just cannot be replayed.
        problem = event_error(event)
        if problem:
            return self._fail(
                data, "invalid_event",
                f"event does not fit the chat contract: {problem}",
            )

        # The backend owns the sequence: allocated atomically on the
        # chat's runtime block, invisible to users. A child's narration
        # is tagged for the audience and rides the chat's own sequence.
        seq = ChatStore().next_event_seq(chat["chat_id"])

        events = ChatEventStore()
        events.append({
            **self._identity(user, chat["chat_id"]),
            "direction": events.OUT,
            "thread": None,
            "seq": seq,
            "event": event,
            "created_at": utc_now(),
        })
        # A bounded tail, pruned as it grows.
        events.prune(chat["chat_id"], seq - self.EVENTS_KEPT)
        # The chat's own word on whether it is working, for the lists
        # that cannot watch its socket.
        name = str(event.get("event"))
        if name == "working":
            ChatStore().set_working(chat["chat_id"], True)
        elif name == "idle":
            ChatStore().set_working(chat["chat_id"], False)
        elif name == "stopped":
            ChatStore().set_working(chat["chat_id"], False)
            ChatStore().set_active_jobs(chat["chat_id"], 0)
        return self._respond(data, {"seq": seq})

    def record(self, data, user):
        """The inbox: an inbound event, durable before absorbed."""
        chat, refusal = self._runtime_chat(data, user, "record events")
        if refusal is not None:
            return refusal
        thread, refusal = self._thread(data)
        if refusal is not None:
            return refusal

        event = self._payload(data).get("event")
        if not isinstance(event, dict) or not isinstance(
            event.get("event"), str
        ) or not event["event"]:
            return self._fail(
                data, "invalid_request",
                "event must be an object with an event name.",
            )
        size = len(json.dumps(event, default=str).encode("utf-8"))
        if size > self.EVENT_MAX_BYTES:
            return self._fail(
                data, "event_too_large",
                f"Event is {size} bytes; the limit is "
                f"{self.EVENT_MAX_BYTES}.",
            )

        chats = ChatStore()
        seq = chats.next_inbox_seq(chat["chat_id"], thread)
        events = ChatEventStore()
        events.append({
            **self._identity(user, chat["chat_id"]),
            "direction": events.IN,
            "thread": thread or None,
            "seq": seq,
            "event": {**event, "seq": seq},
            "created_at": utc_now(),
        })
        # The inbox is pruned behind the mind's bookmark, never ahead
        # of it: an event the runtime has not absorbed is an instruction
        # still owed, however long the runtime has been away, and a
        # fixed tail would have deleted the oldest of them unread.
        events.prune(
            chat["chat_id"],
            min(seq - self.EVENTS_KEPT,
                chats.inbox_cursor(chat["chat_id"], thread)),
            events.IN, thread)
        return self._respond(data, {"seq": seq})

    def since(self, data, user):
        """The inbox past the runtime's bookmark."""
        chat, refusal = self._runtime_chat(data, user, "read the inbox")
        if refusal is not None:
            return refusal
        thread, refusal = self._thread(data)
        if refusal is not None:
            return refusal

        cursor = self._payload(data).get("cursor")
        if not isinstance(cursor, int) or isinstance(cursor, bool):
            cursor = 0
        events = ChatEventStore()
        documents = events.after(
            chat["chat_id"], cursor, self.MAX_LIMIT, events.IN, thread)
        return self._respond(data, {
            "events": [d["event"] for d in documents],
        })

    def list(self, data, user):
        chat, refusal = self._chat_or_refusal(data, user)
        if refusal is not None:
            return refusal

        payload = self._payload(data)
        after = payload.get("after_seq")
        if not isinstance(after, int) or isinstance(after, bool):
            after = None
        limit = self._limit(data, self.DEFAULT_LIMIT, self.MAX_LIMIT)

        documents = ChatEventStore().after(chat["chat_id"], after, limit)
        latest = int(
            ((chat.get("runtime") or {}).get("event_seq")) or 0
        )
        return self._respond(data, {
            "events": [self._public(d) for d in documents],
            "latest_seq": latest,
        })
