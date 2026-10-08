from pymongo.errors import DuplicateKeyError

from contracts.chat import ACTORS, COLUMNS_MAX, PART_TYPES, part_error
from database.stores import ChatMessageStore, ChatStorageStore
from util import new_id, utc_now

from .base import AIController


def message_page(chat_id, limit, query=None):
    """The newest ``limit`` messages of a chat, oldest first, with the
    cursor a client pages backwards with.

    One definition, because two doors serve it: Message:List, which
    pages back, and Chat:Open, which takes the tail. The paging
    mechanics live on the store; this shapes the documents for the
    wire."""
    # The chat's own conversation unless a thread is asked for — a
    # sub-assistant's thread is a record, not part of what the person
    # reads as the chat.
    page = ChatMessageStore().page(chat_id, limit,
                                   {"thread": None, **(query or {})})
    return {
        "messages": [AIController._public(m) for m in page["documents"]],
        "total": page["total"],
        "next_before": page["next_before"],
        "has_more": page["has_more"],
    }


class MessageController(AIController):
    #: ``parent`` is a sub-assistant's goal, spoken by the mind that
    #: spawned it (docs/system/sub-assistants.md).
    ACTORS = set(ACTORS)
    #: The contract's part types (contracts/chat.py). `success` is the
    #: runtime's record of a verified write — machine state the page
    #: keeps on the message and does not render.
    PART_TYPES = set(PART_TYPES)
    DEFAULT_LIMIT = 50
    MAX_LIMIT = 200

    def _part_error(self, chat_id, part):
        """One part's validation problem, or None: this chat's rules
        first, in their own words, then the contract's shape — which
        refuses a field nobody defined, or a malformed source."""
        return self._chat_part_error(chat_id, part) or part_error(part)

    def _chat_part_error(self, chat_id, part):
        """Storage-referencing parts must point at THIS chat's verified
        results — a message can never cite data that does not exist."""
        part_type = part.get("type")
        if part_type not in self.PART_TYPES:
            return f"unknown part type '{part_type}'"

        if part_type == "markdown":
            if not isinstance(part.get("content"), str) or not part["content"]:
                return "markdown parts require content"
            return None

        if part_type == "file":
            if not str(part.get("resource_ref") or ""):
                return "file parts require resource_ref"
            return None

        if part_type == "success":
            if not isinstance(part.get("text"), str) or not part["text"]:
                return "success parts require text"
            storage_ref = str(part.get("storage_ref") or "")
            if storage_ref and not ChatStorageStore().exists_in_chat(
                    storage_ref, chat_id):
                return (f"storage_ref '{storage_ref}' is not a stored "
                        f"result of this chat")
            return None

        # table / graph
        storage_ref = str(part.get("storage_ref") or "")
        if not storage_ref:
            return f"{part_type} parts require storage_ref"
        if not ChatStorageStore().exists_in_chat(storage_ref, chat_id):
            return f"storage_ref '{storage_ref}' is not a stored result of this chat"
        if part.get("path") is not None and not isinstance(part["path"], str):
            return "path must be a string"
        columns = part.get("columns")
        if columns is not None and (
                not isinstance(columns, list) or len(columns) > COLUMNS_MAX
                or not all(isinstance(c, str) and c for c in columns)):
            return f"columns must be a list of up to {COLUMNS_MAX} column names"
        return None

    def create(self, data, user):
        # Only the delegated runtime creates messages — the frontend's
        # input travels over the chat socket, so a browser principal
        # writing messages (any actor it likes) would be forgery.
        chat, refusal = self._runtime_chat(data, user, "create messages")
        if refusal is not None:
            return refusal

        thread, refusal = self._thread(data)
        if refusal is not None:
            return refusal

        payload = self._payload(data)
        actor = str(payload.get("actor") or "")
        if actor not in self.ACTORS:
            return self._fail(
                data, "invalid_actor",
                "actor must be user, ai, system, or parent."
            )

        parts = payload.get("parts")
        if not isinstance(parts, list) or not parts or not all(
            isinstance(part, dict) for part in parts
        ):
            return self._fail(
                data, "invalid_parts", "parts must be a non-empty list of objects."
            )
        for part in parts:
            problem = self._part_error(chat["chat_id"], part)
            if problem:
                return self._fail(data, "invalid_parts", problem)

        # Idempotency, by the page's own name for a submission: a resend
        # after a lost socket is the message it already made. The
        # unique index answers the race the pre-check cannot.
        messages = ChatMessageStore()
        client_message_id = str(payload.get("client_message_id") or "")
        if client_message_id:
            existing = messages.by_client_message_id(
                chat["chat_id"], client_message_id)
            if existing is not None:
                return self._respond(data, {
                    "message": self._public(existing), "created": False,
                })

        document = {
            **self._identity(user, chat["chat_id"]),
            "message_id": f"msg_{new_id()}",
            "actor": actor,
            "thread": thread or None,
            "parts": parts,
            "client_message_id": client_message_id or None,
            "sequence": self._next_sequence(chat["chat_id"]),
            "created_at": utc_now(),
        }
        try:
            messages.insert(document)
        except DuplicateKeyError:
            existing = (
                messages.by_client_message_id(
                    chat["chat_id"], client_message_id)
                if client_message_id else None)
            if existing is not None:
                return self._respond(data, {
                    "message": self._public(existing), "created": False,
                })
            raise
        if actor == "ai" and not thread:
            # The assistant answered; a person who is not looking is told.
            from server.notifications import Notifier

            Notifier().assistant_said(chat, document)
        return self._respond(data, {
            "message": self._public(document), "created": True,
        })

    def list(self, data, user):
        if user.get("principal_type") == "runtime":
            chat, refusal = self._runtime_chat(data, user, "read a transcript")
        else:
            chat, refusal = self._chat_or_refusal(data, user)
        if refusal is not None:
            return refusal

        thread, refusal = self._thread(data)
        if refusal is not None:
            return refusal

        payload = self._payload(data)
        limit = self._limit(data, self.DEFAULT_LIMIT, self.MAX_LIMIT)
        query = {"thread": thread or None}

        # The page before a sequence — how the person scrolls back.
        before = payload.get("before")
        if isinstance(before, int) and not isinstance(before, bool):
            query["sequence"] = {"$lt": before}

        return self._respond(data, message_page(chat["chat_id"], limit, query))
