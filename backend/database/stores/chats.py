"""The chat domain's stores — every Mongo touch the AI endpoint
controllers make, in one place.

    ai_chats         the chat document: config, plan, the mind's state
                     (docs/system/chat-session.md), and the runtime block
                     (lease, event_seq, inbox_seq)
    ai_messages      the conversation, sequenced per chat
    ai_chat_events   two logs in one collection, told apart by
                     ``direction``: "out" is the bounded, replayable
                     narration tail; "in" is the inbox — events durable
                     before the runtime absorbs them
    ai_chat_storage  verified function results, referenced by parts
    ai_approvals     the cards: what the runtime asked, what the person
                     decided
    ai_schedules     the clock's rows, per chat, the runtime's to write

These stores are deliberately thin: queries and writes, exactly as the
controllers made them when they held the collections themselves. What a
status means, what a valid payload looks like, and what a caller is told
stay at the edges — the same split as the data layer.

Chats are addressed by ``chat_id`` throughout (unique per chat), never
by ``_id``: the callers all hold the chat document they were authorized
against, and one key means no method needs to know which the caller has.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument

from database.stores.base import MongoStore
from util import utc_now


class ChatStore(MongoStore):
    COLLECTION = "ai_chats"

    def insert(self, document: Dict[str, Any]) -> None:
        self.col.insert_one(document)

    def for_identity(self, identity: Dict[str, Any]):
        """The chat as the CALLER may see it: looked up by the full
        identity (chat_id, org_id, user_id), so somebody else's chat is
        indistinguishable from one that does not exist."""
        return self.col.find_one(identity)

    def by_chat_id(self, chat_id: str):
        """No reader in the question — for a caller that already holds
        the authorization, such as a test reading the document back."""
        return self.col.find_one({"chat_id": str(chat_id or "")})

    def list_for(self, org_id: str, user_id: str) -> List[Dict[str, Any]]:
        return list(
            self.col.find({"org_id": org_id, "user_id": user_id})
            .sort("updated_at", -1)
        )

    def list_summaries_for(
        self, org_id: str, user_id: str
    ) -> List[Dict[str, Any]]:
        """Small rows for conversation pickers.

        A chat document also carries the assistant's durable state and its
        nested thread states. Reading those large fields just to render a
        title made history slower as conversations grew. This projection is
        covered by ``owner_updated`` and keeps the list response constant in
        size per chat.
        """
        return list(
            self.col.find(
                {"org_id": org_id, "user_id": user_id},
                {
                    "_id": 0,
                    "chat_id": 1,
                    "title": 1,
                    "status": 1,
                    "message_sequence": 1,
                    "created_at": 1,
                    "updated_at": 1,
                    # What a row must say about attention: whether the
                    # runtime is working, jobs it still holds, and when
                    # the person last had the chat in front of them.
                    "runtime.working": 1,
                    "runtime.active_jobs": 1,
                    "runtime.seen_at": 1,
                },
            ).sort("updated_at", -1)
        )

    # ── attention ───────────────────────────────────────────────────────

    def mark_seen(self, chat_id: str) -> None:
        """The person had this chat in front of them now: anything that
        happens after this is news, anything before is not."""
        self.col.update_one({"chat_id": chat_id},
                            {"$set": {"runtime.seen_at": utc_now()}})

    def set_working(self, chat_id: str, working: bool) -> None:
        """Whether a turn is advancing — the runtime's working and idle
        events, kept as one flag so a list can say so."""
        self.col.update_one({"chat_id": chat_id},
                            {"$set": {"runtime.working": bool(working)}})

    def set_active_jobs(self, chat_id: str, count: int) -> None:
        self.col.update_one({"chat_id": chat_id},
                            {"$set": {"runtime.active_jobs": int(count)}})

    @staticmethod
    def attention_of(row: Dict[str, Any], cards: int) -> Dict[str, Any]:
        """What this chat needs from the person, from its row: work
        going on, cards waiting, and news since they last looked. A
        chat never seen through a socket is not news — every chat
        would otherwise light up at once."""
        runtime = row.get("runtime") or {}
        seen_at = runtime.get("seen_at")
        updated_at = row.get("updated_at")
        unseen = bool(seen_at and updated_at and updated_at > seen_at)
        return {
            "working": bool(runtime.get("working")) or int(runtime.get("active_jobs") or 0) > 0,
            "cards": int(cards or 0),
            "unseen": unseen,
        }

    def activity_for(self, owner: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The owner's chats with only what the activity page reads —
        the title and each mind's jobs table, the children's too. A
        mind's transcript stays where it is."""
        return list(
            self.col.find(owner, {
                "chat_id": 1, "title": 1, "updated_at": 1,
                "state.jobs": 1, "threads": 1,
            }).sort("updated_at", -1)
        )

    def edit(self, chat_id: str, changes: Dict[str, Any]) -> None:
        """Apply field changes and stamp updated_at."""
        self.col.update_one(
            {"chat_id": chat_id},
            {"$set": {**changes, "updated_at": utc_now()}},
        )

    def delete_chat(self, chat_id: str) -> None:
        self.col.delete_one({"chat_id": chat_id})

    # ── sequences — the backend owns both of them ───────────────────────

    def next_sequence(self, chat_id: str) -> int:
        """The chat-wide message ordering counter, atomically.

        A message is the chat's activity: the same write stamps
        ``updated_at``, which the chat list orders on — so a chat rises
        when it is spoken in, not only when its settings are edited."""
        chat = self.col.find_one_and_update(
            {"chat_id": chat_id},
            {"$inc": {"message_sequence": 1},
             "$set": {"updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )
        return int(chat["message_sequence"])

    def next_event_seq(self, chat_id: str) -> int:
        """The event log's counter, on the chat's runtime block."""
        updated = self.col.find_one_and_update(
            {"chat_id": chat_id},
            {"$inc": {"runtime.event_seq": 1}},
            return_document=ReturnDocument.AFTER,
        )
        return int((updated.get("runtime") or {}).get("event_seq") or 1)

    # ── the plan ────────────────────────────────────────────────────────

    def set_plan(self, chat_id: str, plan: Dict[str, Any],
                 thread: str = "") -> None:
        self.col.update_one(
            {"chat_id": chat_id},
            {"$set": {self._field("plan", thread): plan}},
        )

    # ── the mind, and the inbox counter — per thread ────────────────────
    #
    # A sub-assistant's records live on its parent's chat under
    # ``threads.<thread>`` (docs/system/chat-session.md): same document, same
    # credential, its own state, plan and inbox counter.

    @staticmethod
    def _field(name: str, thread: str) -> str:
        return f"threads.{thread}.{name}" if thread else name

    def set_state(self, chat_id: str, state: Dict[str, Any],
                  thread: str = "") -> None:
        """The assistant's state, replaced whole — one writer, every
        beat, no version dance (docs/system/chat-session.md)."""
        changes = {self._field("state", thread): state,
                   self._field("state_saved_at", thread): utc_now()}
        if not thread:
            # The root's jobs include its children (a spawn is a job),
            # so this one count says whether background work goes on.
            changes["runtime.active_jobs"] = sum(
                1 for job in (state.get("jobs") or {}).values()
                if isinstance(job, dict)
                and job.get("status") in ("running", "waiting_approval"))
        self.col.update_one({"chat_id": chat_id}, {"$set": changes})

    @classmethod
    def state_of(cls, chat: Dict[str, Any], thread: str = ""):
        holder = ((chat.get("threads") or {}).get(thread) or {}) if thread \
            else chat
        state = holder.get("state")
        return state if isinstance(state, dict) else None

    def next_inbox_seq(self, chat_id: str, thread: str = "") -> int:
        """The inbox's counter — the runtime's cursor is a bookmark
        into this sequence, so it is the backend's to allocate."""
        field = (f"runtime.threads.{thread}.inbox_seq" if thread
                 else "runtime.inbox_seq")
        updated = self.col.find_one_and_update(
            {"chat_id": chat_id},
            {"$inc": {field: 1}},
            return_document=ReturnDocument.AFTER,
        )
        runtime = updated.get("runtime") or {}
        if thread:
            runtime = (runtime.get("threads") or {}).get(thread) or {}
        return int(runtime.get("inbox_seq") or 1)

    def inbox_cursor(self, chat_id: str, thread: str = "") -> int:
        """How far the mind has read its inbox — the ``cursor`` of its
        last persisted state, 0 for a mind never persisted. Everything
        above it is unabsorbed and must be kept, whatever else is
        pruned."""
        field = self._field("state", thread) + ".cursor"
        document = self.col.find_one({"chat_id": chat_id}, {field: 1}) or {}
        holder = ((document.get("threads") or {}).get(thread) or {}) \
            if thread else document
        cursor = (holder.get("state") or {}).get("cursor")
        return int(cursor) if isinstance(cursor, int) else 0


class ChatMessageStore(MongoStore):
    COLLECTION = "ai_messages"

    def insert(self, document: Dict[str, Any]) -> None:
        """DuplicateKeyError travels up — the client_message_id
        idempotency race is the caller's to resolve into 'created:
        False'."""
        self.col.insert_one(document)

    def by_client_message_id(self, chat_id: str, client_message_id: str):
        """The message a page's own submission id already made in this
        chat, if any — the unique index `chat_client_message_unique`
        is what makes there be at most one."""
        return self.col.find_one({"chat_id": chat_id,
                                  "client_message_id": client_message_id})

    def page(self, chat_id: str, limit: int,
             query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """The newest ``limit`` messages, oldest first, with the cursor a
        client pages backwards with. ``has_more`` deliberately ignores
        the range — it answers "is there anything older in this chat",
        which is what the cursor is for."""
        full = {"chat_id": chat_id, **(query or {})}
        newest_first = list(
            self.col.find(full).sort("sequence", -1).limit(limit)
        )
        documents = list(reversed(newest_first))
        oldest = documents[0].get("sequence") if documents else None
        has_more = bool(oldest and self.col.find_one({
            "chat_id": chat_id, "sequence": {"$lt": oldest},
        }))
        return {
            "documents": documents,
            "total": self.col.count_documents(full),
            "next_before": oldest if has_more else None,
            "has_more": has_more,
        }

    def delete_for_chat(self, identity: Dict[str, Any]) -> None:
        self.col.delete_many(identity)


class ChatEventStore(MongoStore):
    COLLECTION = "ai_chat_events"

    OUT = "out"
    IN = "in"

    def append(self, document: Dict[str, Any]) -> None:
        self.col.insert_one(document)

    def prune(self, chat_id: str, below_seq: int,
              direction: str = OUT, thread: str = "") -> None:
        """The bounded tail: everything at or below the cut goes."""
        self.col.delete_many({
            "chat_id": chat_id, "seq": {"$lte": below_seq},
            **self._where(direction, thread),
        })

    def after(self, chat_id: str, after_seq: Optional[int],
              limit: int, direction: str = OUT,
              thread: str = "") -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {"chat_id": chat_id,
                                 **self._where(direction, thread)}
        if after_seq is not None:
            query["seq"] = {"$gt": after_seq}
        return list(self.col.find(query).sort("seq", 1).limit(limit))

    @staticmethod
    def _where(direction: str, thread: str) -> Dict[str, Any]:
        return {"thread": thread or None, "direction": direction}

    def delete_for_chat(self, identity: Dict[str, Any]) -> None:
        self.col.delete_many(identity)


class ChatStorageStore(MongoStore):
    COLLECTION = "ai_chat_storage"

    def insert(self, document: Dict[str, Any]) -> None:
        self.col.insert_one(document)

    def get_scoped(self, storage_id: str, scope: Dict[str, Any]):
        """By id within whatever scope the caller is entitled to — a
        delegation reads only its chat's results; a person reads across
        their own chats."""
        return self.col.find_one({"storage_id": storage_id, **scope})

    def exists_in_chat(self, storage_id: str, chat_id: str) -> bool:
        """Whether a part's storage_ref names a stored result of THIS
        chat — a message can never cite data that does not exist."""
        return self.col.find_one({
            "storage_id": storage_id, "chat_id": chat_id,
        }) is not None

    def delete_for_chat(self, identity: Dict[str, Any]) -> None:
        self.col.delete_many(identity)


class ApprovalStore(MongoStore):
    """The cards (docs/system/chat-session.md): the runtime opens one when a
    call parks on a human, the person decides it, the relay carries the
    decision back. One record per ask; a decision lands once."""

    COLLECTION = "ai_approvals"

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    #: A question's two endings (call.ask): the person answered, or
    #: nobody did in time and the runtime closed it.
    ANSWERED = "answered"
    EXPIRED = "expired"

    def open(self, document: Dict[str, Any]) -> None:
        self.col.insert_one(document)

    def owned(self, approval_id: str, owner: Dict[str, Any]):
        return self.col.find_one({"approval_id": approval_id, **owner})

    def in_chat(self, approval_id: str, chat_id: str):
        return self.col.find_one({"approval_id": approval_id,
                                  "chat_id": chat_id})

    def pending_in_chat(self, chat_id: str) -> List[Dict[str, Any]]:
        return list(
            self.col.find({"chat_id": chat_id, "status": self.PENDING})
            .sort("requested_at", 1)
        )

    def code_allowed_in_chat(self, chat_id: str,
                             not_by: str) -> List[Dict[str, Any]]:
        """The code cards of a chat that were answered allow, by
        anybody but ``not_by`` — the Safety setting settles cards too,
        and what it let through is no person's word."""
        return list(self.col.find({
            "chat_id": chat_id, "kind": "question",
            "request.expects": "code", "status": self.ANSWERED,
            "answer": "allow", "resolved_by": {"$ne": not_by},
        }).sort("requested_at", 1))

    def pending_counts_for(self, owner: Dict[str, Any]) -> Dict[str, int]:
        """chat_id -> how many cards wait on this person there."""
        counts: Dict[str, int] = {}
        for card in self.col.find({**owner, "status": self.PENDING},
                                  {"_id": 0, "chat_id": 1}):
            chat_id = str(card.get("chat_id") or "")
            counts[chat_id] = counts.get(chat_id, 0) + 1
        return counts

    def pending_for(self, owner: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Every card still waiting on this person, across their chats."""
        return list(
            self.col.find({**owner, "status": self.PENDING})
            .sort("requested_at", 1)
        )

    def expire_pending_in_chat(self, chat_id: str) -> int:
        """Every card still waiting in a chat, closed as expired — the
        kill switch's half that needs no runtime. Returns how many."""
        result = self.col.update_many(
            {"chat_id": chat_id, "status": self.PENDING},
            {"$set": {"status": self.EXPIRED, "resolved_at": utc_now()}},
        )
        return int(result.modified_count)

    def decide(self, approval_id: str, changes: Dict[str, Any]) -> bool:
        """Lands only while pending — a second decision is refused, not
        recorded over the first."""
        result = self.col.update_one(
            {"approval_id": approval_id, "status": self.PENDING},
            {"$set": changes},
        )
        return result.modified_count > 0

    def delete_for_chat(self, identity: Dict[str, Any]) -> None:
        self.col.delete_many(identity)


class ScheduleStore(MongoStore):
    """The clock's rows, one document per chat holding its handful.
    Two hands write them — the runtime as its clock moves, and the
    person's doors on the schedules page — so every write changes ONE
    row, in one step of the database's: a pause on the page and a fire
    in the runtime at the same moment each keep what they wrote."""

    COLLECTION = "ai_schedules"
    #: How many rows one chat may hold.
    MAX_ROWS = 50

    def rows(self, chat_id: str) -> List[Dict[str, Any]]:
        document = self.col.find_one({"chat_id": chat_id})
        return list((document or {}).get("rows") or [])

    def rows_for(self, owner: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The owner's holders — each chat's rows, whole."""
        return list(self.col.find({**owner, "rows.0": {"$exists": True}},
                                  {"chat_id": 1, "rows": 1}))

    def holders(self) -> List[Dict[str, Any]]:
        """Every chat holding at least one row — what a boot re-dials."""
        return list(self.col.find(
            {"rows.0": {"$exists": True}},
            {"chat_id": 1, "org_id": 1, "user_id": 1}))

    def add(self, identity: Dict[str, Any], row: Dict[str, Any]) -> bool:
        """One more row. False when the chat already holds as many as
        it may — after the one-shots that already ran, which nothing
        will ever fire again, have made what room they can."""
        chat = {"chat_id": identity["chat_id"]}
        self.col.update_one(chat, {"$setOnInsert": {**identity, "rows": []}},
                            upsert=True)
        full = {**chat, f"rows.{self.MAX_ROWS - 1}": {"$exists": True}}
        if self.col.count_documents(full, limit=1):
            self.col.update_one(chat, {"$pull": {"rows": {
                "enabled": False, "cron": "", "every_seconds": None,
                "last_run_at": {"$ne": None}}}})
        added = self.col.update_one(
            {**chat, f"rows.{self.MAX_ROWS - 1}": {"$exists": False}},
            {"$push": {"rows": row}, "$set": {"updated_at": utc_now()}})
        return added.matched_count == 1

    def change(self, chat_id: str, schedule_id: str,
               fields: Dict[str, Any]) -> bool:
        """These fields of one row, and no others. False when the row
        is no longer there."""
        changed = self.col.update_one(
            {"chat_id": chat_id, "rows.schedule_id": schedule_id},
            {"$set": {**{f"rows.$.{name}": value
                         for name, value in fields.items()},
                      "updated_at": utc_now()}})
        return changed.matched_count == 1

    def remove(self, chat_id: str, schedule_id: str) -> None:
        self.col.update_one(
            {"chat_id": chat_id},
            {"$pull": {"rows": {"schedule_id": schedule_id}},
             "$set": {"updated_at": utc_now()}})

    def delete_for_chat(self, identity: Dict[str, Any]) -> None:
        self.col.delete_many(identity)

    def pause_for_user(self, org_id: str, user_id: str) -> int:
        """Every row of every chat this person owns, paused — nothing
        keeps acting unattended for someone who may not act. Returns
        how many rows were live."""
        owner = {"org_id": str(org_id or ""), "user_id": str(user_id or "")}
        paused = sum(1 for holder in self.rows_for(owner)
                     for row in holder.get("rows") or []
                     if row.get("enabled", True))
        self.col.update_many(
            {**owner, "rows.0": {"$exists": True}},
            {"$set": {"rows.$[].enabled": False, "updated_at": utc_now()}})
        return paused

    def count_for_user(self, org_id: str, user_id: str) -> int:
        owner = {"org_id": str(org_id or ""), "user_id": str(user_id or "")}
        return sum(len(h.get("rows") or []) for h in self.rows_for(owner))
