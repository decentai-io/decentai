"""The chat itself: the record every other controller here hangs off.

Two audiences, one document. A person creates a chat, configures it,
reads it back and eventually deletes it; the runtime writes the
operational parts of it — its state, the plan — and never sees the block it writes them into, because `public`
strips `runtime` on the way out.

Configuration is where the care is, and none of it is written here.
`api/services/chat_session/settings` owns what each setting means, what a valid one
looks like and where a chat's comes from when nobody said — the same
objects the preferences page asks, so the two doors cannot drift.
"""

from database.stores import (
    ApprovalStore,
    ChatEventStore,
    ChatMessageStore,
    ChatStorageStore,
    ChatStore,
    RuntimeSessionStore,
    ScheduleStore,
)
from server.setup.app_state import get_runtime_clients
from util import new_id, utc_now
from contracts.chat import (
    CHAT_PROTOCOL_VERSION, PLAN_MAX_STEPS, PLAN_STEP_MAX_CHARS,
)

from .base import AIController
from .message_controller import MessageController, message_page


class ChatController(AIController):
    ATTACHMENT_FOLDER = "chat_artifacts"

    def __init__(self):
        from api.services.data_layer import FileController
        from server.custom_logging import CustomLoggerFactory
        self.files = FileController()
        self.chats = ChatStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _public(document):
        """Chats additionally hide `runtime` (operational counters) and
        `state` (the assistant's mind — its transcript and frame, the
        runtime's to read; a person sees its consequences as messages,
        cards and the plan)."""
        return {
            key: value for key, value in document.items()
            if key not in ("_id", "runtime", "state", "state_saved_at")
        }

    def _new_chat(self, user, payload):
        """(document, "") or (None, problem) — the one way a chat is
        born, shared by `create` and `open`."""
        from api.services.chat_session.settings import ChatSettings

        # What was asked for, checked; and this person's defaults for
        # everything they left out. A choice made once, on the
        # preferences page, is not a choice to make again per conversation.
        config, problem = ChatSettings().for_new_chat(user, payload.get("config"))
        if problem:
            return None, problem

        now = utc_now()
        document = {
            **self._identity(user, f"chat_{new_id()}"),
            "title": str(payload.get("title") or "New chat")[:200],
            "status": "active",
            "config": config,
            "message_sequence": 0,
            "created_at": now,
            "updated_at": now,
        }
        self.chats.insert(document)
        return document, ""

    def create(self, data, user):
        document, problem = self._new_chat(user, self._payload(data))
        if problem:
            return self._fail(data, "invalid_request", problem)
        return self._respond(data, {"chat": self._public(document)})

    def open(self, data, user):
        """The one door a chat session goes through: create or resume,
        with everything resolved.

        Returns the chat, its SESSION CONTRACT — the model that will
        think, the agents and functions this person may call, skills,
        budgets, and what the caller may do here — and the snapshot a
        client rehydrates from. Anything missing (no visible model, no
        agents) is a sentence in the contract NOW, at the door, never a
        failure discovered mid-turn."""
        from api.services.chat_session import SessionContract

        payload = self._payload(data)
        chat_id = str(payload.get("chat_id") or "")
        if chat_id:
            chat, refusal = self._chat_or_refusal(data, user, chat_id)
            if refusal is not None:
                return refusal
            chat = self._adopt_timezone(chat, payload.get("timezone"))
            created = False
        else:
            chat, problem = self._new_chat(user, payload)
            if problem:
                return self._fail(data, "invalid_request", problem)
            created = True

        return self._respond(data, {
            "created": created,
            "contract": SessionContract().of(user, chat),
            "ws": f"/chats/{chat['chat_id']}",
            **self._snapshot_payload(data, chat),
        })

    def _adopt_timezone(self, chat, offered):
        """A chat from before zones were kept takes the page's, once.
        A chat that has one keeps it: a person opening a conversation
        from another country has not asked its reminders to move."""
        from api.services.chat_session.settings import Timezone

        config = dict(chat.get("config") or {})
        if config.get("timezone") or not Timezone.is_zone(offered):
            return chat
        config["timezone"] = str(offered).strip()
        self.chats.edit(chat["chat_id"], {"config": config})
        return {**chat, "config": config}

    def contract(self, data, user):
        """What this chat may do, in the runtime's own shape — the one
        answer authority comes from (docs/system/chat-session.md, and the
        runtime's docs/reference/session-door.md). The credential the runtime
        called with is not a statement of rights; this is."""
        from api.services.chat_session import SessionContract

        chat, refusal = self._runtime_chat(data, user, "read the contract")
        if refusal is not None:
            return refusal

        session = SessionContract().of(user, chat)
        budgets = session.get("budgets") or {}
        agents = session.get("agents") or {}
        mcp = session.get("mcp") or []
        return self._respond(data, {
            # Each agent as the approval names it — the ref everything
            # outside the package speaks, and what a runtime needs to
            # pull and verify the package (docs/system/agent-code.md).
            "agents": [
                {"agent_id": ref,
                 "name": info.get("name"),
                 "local_agent_id": info.get("local_agent_id"),
                 "package_digest": info.get("package_digest"),
                 "manifest_hash": info.get("manifest_hash")}
                for ref, info in sorted(agents.items())
            ],
            # What the person was granted of the agents — and their own
            # MCP servers, which are theirs to call without anybody's
            # grant: each tool is still held to its level.
            "grants": list(session.get("permissions") or []) + [
                {"effect": "allow", "functions": [f"{server['ref']}.*.*"]}
                for server in mcp],
            "mcp": mcp,
            "chat_level": session.get("trust"),
            "timezone": session.get("timezone"),
            "llm": session.get("llm"),
            "llm_missing": session.get("llm_missing"),
            # The setting is "max_turns" (settings/turns.py): how long
            # a turn may think. The runtime counts that in beats.
            "max_beats": budgets.get("max_turns"),
            # How many skills the frame lists (settings/skills_cap.py);
            # 0 lists every one.
            "max_skills": budgets.get("max_skills"),
            # WHICH skills, when the chat narrowed them (enabled_skills):
            # a list of refs, or None for every skill the person can see.
            "skills": session.get("skills"),
            # How the runtime finds the right agent among many: the
            # organization's numbers and its embedding model, if any.
            "routing": session.get("routing"),
            # What the deployment lets agents do, as far as the runtime
            # enforces it: blocked sites and the list of packages.
            "safety": session.get("safety"),
        })

    def get(self, data, user):
        chat, refusal = self._chat_or_refusal(data, user)
        if refusal is not None:
            return refusal

        from api.services.chat_session.settings import ChatSettings

        # What is SERVED is made safe to act on: a stored value that
        # cannot be read becomes the standard one rather than nonsense.
        settings = ChatSettings()
        public = self._public(chat)
        public["config"] = settings.as_served(public.get("config"))

        return self._respond(data, {
            "chat": public,
            # Beside the chat, never inside its config: the page writes
            # the whole config back when trust changes, so a computed
            # value placed there would be stored as a person's choice.
            "budgets": settings.budgets_of(public["config"]),
        })

    def _snapshot_payload(self, data, chat):
        """The rehydration surface `open` serves beside the contract:
        records read after the chat's authorization check, with the
        event high-water mark replay continues from."""
        from api.services.chat_session.settings import ChatSettings

        messages = message_page(
            chat["chat_id"],
            self._limit(data, MessageController.DEFAULT_LIMIT,
                        MessageController.MAX_LIMIT),
        )

        # The cards a late audience must find; the mind's own jobs travel
        # on the runtime's hello, not here.
        approvals = [AIController._public(a) for a in
                     ApprovalStore().pending_in_chat(chat["chat_id"])]
        latest = int(((chat.get("runtime") or {}).get("event_seq")) or 0)

        settings = ChatSettings()
        public = self._public(chat)
        public["config"] = settings.as_served(public.get("config"))

        return {
            "protocol_version": CHAT_PROTOCOL_VERSION,
            "chat": public,
            "budgets": settings.budgets_of(public["config"]),
            "messages": messages,
            "approvals": approvals,
            "latest_event_seq": latest,
        }

    PLAN_STATUSES = ("pending", "active", "done", "blocked")

    def plan(self, data, user):
        """What this turn set out to do, and how far it got.

        The engine writes it; the user never does. A plan the user could
        edit would stop being a record of what happened and become a
        second opinion about it — and the whole point is that the model
        cannot claim a step is finished when the work behind it is not.

        Replaced wholesale rather than patched: the engine holds the
        turn's whole plan in hand, and one writer replacing one document
        has no races to lose. An empty list clears it, which is what a
        finished plan does.
        """
        chat, refusal = self._runtime_chat(data, user, "write the chat plan")
        if refusal is not None:
            return refusal
        thread, refusal = self._thread(data)
        if refusal is not None:
            return refusal

        payload = self._payload(data)

        steps = payload.get("steps")
        if not isinstance(steps, list):
            return self._fail(
                data, "invalid_request", "steps must be a list.",
            )
        if len(steps) > PLAN_MAX_STEPS:
            return self._fail(
                data, "plan_too_long",
                f"a plan may have at most {PLAN_MAX_STEPS} steps.",
            )

        cleaned = []
        for step in steps:
            if not isinstance(step, dict):
                return self._fail(
                    data, "invalid_request", "each step must be an object.",
                )
            text = str(step.get("text") or "").strip()
            status = str(step.get("status") or "pending").strip().lower()
            if not text:
                return self._fail(
                    data, "invalid_request", "each step needs text.",
                )
            if status not in self.PLAN_STATUSES:
                return self._fail(
                    data, "invalid_request",
                    f"status must be one of {', '.join(self.PLAN_STATUSES)}.",
                )
            # What the runtime proved for the item rides with it — its
            # id, the evidence refs, what blocks it, what it waits on —
            # kept as strings, bounded, never interpreted here: the
            # runtime is the one that checked them against its trace.
            cleaned.append({
                "id": str(step.get("id") or "")[:16],
                "text": text[: PLAN_STEP_MAX_CHARS],
                "status": status,
                "evidence": [str(r)[:64] for r in (step.get("evidence") or [])
                             if isinstance(r, str)][:50],
                "blocker": str(step.get("blocker") or "")[: PLAN_STEP_MAX_CHARS],
                "depends_on": [str(d)[:16] for d in (step.get("depends_on") or [])
                               if isinstance(d, str)][:20],
                "verified": bool(step.get("verified")),
            })

        plan = {"steps": cleaned, "updated_at": utc_now()}
        self.chats.set_plan(chat["chat_id"], plan, thread)
        return self._respond(data, {"plan": plan})

    async def stop(self, data, user):
        """The stop button: cooperative, honored by the assistant between
        beats — a person's own frame, carried by the relay.

        With ``force``, the kill switch: the cards still waiting in the
        chat are expired here first, so the record is quiet whatever
        the runtime is doing, and the runtime is told to end the run,
        its jobs and its browser where they stand."""
        chat, refusal = self._chat_or_refusal(data, user)
        if refusal is not None:
            return refusal
        force = bool(self._payload(data).get("force"))
        expired = 0
        if force:
            from database.stores import ApprovalStore

            expired = ApprovalStore().expire_pending_in_chat(chat["chat_id"])
            self.logger.info(
                f"{user.get('email')} stopped everything in {chat['chat_id']} "
                f"({expired} card(s) expired)")
        delivered = await get_runtime_clients().send(
            chat["chat_id"], user,
            {"event": "stop", "force": True} if force else {"event": "stop"})
        return self._respond(data, {"delivered": bool(delivered), "expired": expired})

    def title(self, data, user):
        """The chat's name from its content, by the runtime — kept
        unless the person named the chat themselves."""
        chat, refusal = self._runtime_chat(data, user, "name chats")
        if refusal is not None:
            return refusal
        title = str(self._payload(data).get("title") or "").strip()[:200]
        if not title:
            return self._fail(data, "invalid_request", "title is required.")
        if chat.get("title_by") == "person":
            return self._respond(data, {"kept": False, "title": chat.get("title")})
        self.chats.edit(chat["chat_id"], {
            "title": title, "title_by": "model", "updated_at": utc_now()})
        return self._respond(data, {"kept": True, "title": title})

    def update(self, data, user):
        payload = self._payload(data)
        chat, refusal = self._chat_or_refusal(data, user)
        if refusal is not None:
            return refusal
        changes = {
            key: payload[key] for key in ("title", "config") if key in payload
        }
        if "title" in changes:
            # A name the person typed stands: the runtime's naming
            # (AI:Chat:Title) leaves it alone from now on.
            changes["title"] = str(changes["title"] or "")[:200]
            changes["title_by"] = "person"
        if "config" in changes:
            from api.services.chat_session.settings import ChatSettings

            config, problem = ChatSettings().for_update(user, changes["config"])
            if problem:
                return self._fail(data, "invalid_request", problem)
            changes["config"] = config
        changes["updated_at"] = utc_now()
        self.chats.edit(chat["chat_id"], changes)
        chat.update(changes)
        return self._respond(data, {"chat": self._public(chat)})

    def list(self, data, user):
        chats = self.chats.list_summaries_for(
            user["org_id"], user["user_id"]
        )
        # What each chat needs from the person, on the row: work going
        # on, cards waiting, news since they last looked.
        from database.stores import ApprovalStore

        cards = ApprovalStore().pending_counts_for(self._owner(user))
        for chat in chats:
            chat["attention"] = ChatStore.attention_of(
                chat, cards.get(str(chat.get("chat_id") or ""), 0))
            chat.pop("runtime", None)
        return self._respond(data, {"chats": chats})

    def uploadfile(self, data, user):
        """Upload a file FOR this chat: stored in the shared files domain,
        stamped as a chat attachment, living in the chat_artifacts folder
        — and dying with the chat."""
        payload = self._payload(data)
        chat, refusal = self._chat_or_refusal(data, user)
        if refusal is not None:
            return refusal

        filename = str(payload.get("filename") or "")
        content_base64 = payload.get("content_base64")
        if not filename or content_base64 is None:
            return self._fail(
                data, "invalid_request",
                "filename and content_base64 are required.",
            )

        result, status = self.files.upload({"data": {
            "filename": filename,
            "content_base64": content_base64,
            "folder": self.ATTACHMENT_FOLDER,
            "meta": {
                "file_kind": "chat_attachment",
                "attachment_mode": "uploaded_for_chat",
                "chat_id": chat["chat_id"],
            },
        }}, user)
        if status != 200:
            return self._fail(
                data, "upload_failed",
                str(result.get("error") or "Upload failed."), status,
            )
        return self._respond(data, {"resource": result["resource"]})

    def archive(self, data, user):
        return self._set_status(data, user, "archived")

    def restore(self, data, user):
        return self._set_status(data, user, "active")

    def _set_status(self, data, user, status):
        chat, refusal = self._chat_or_refusal(data, user)
        if refusal is not None:
            return refusal
        self.chats.edit(chat["chat_id"], {"status": status})
        chat["status"] = status
        return self._respond(data, {"chat": self._public(chat)})

    async def delete(self, data, user):
        chat, refusal = self._chat_or_refusal(data, user)
        if refusal is not None:
            return refusal
        await self._erase(chat, user)
        return self._respond(data, {"deleted": True})

    async def erase_for_user(self, principal) -> int:
        """Every chat one person owns, with the whole cascade — the
        hand-over when they leave. ``principal`` is the leaver, so the
        visibility and creator rules see their own things."""
        removed = 0
        for chat in self.chats.list_for(principal["org_id"], principal["user_id"]):
            await self._erase(chat, principal)
            removed += 1
        return removed

    async def _erase(self, chat, user) -> None:
        chat_id = chat["chat_id"]
        identity = self._identity(user, chat_id)

        # The clock first: the runtime holds the chat's rows in memory,
        # so it is told they are gone while the dial still stands.
        schedules = ScheduleStore()
        if schedules.rows(chat_id):
            schedules.delete_for_chat(identity)
            await get_runtime_clients().send(
                chat_id, user, {"event": "schedules_changed"})

        # Then the live side: nothing may keep acting for a chat being
        # erased — the runtime connection dies and the delegation rows go
        # (which also kills any outstanding runtime access token).
        await get_runtime_clients().close(chat_id, user)
        RuntimeSessionStore().delete_for_chat(user["user_id"], chat_id)

        for satellite in (
            ChatMessageStore(), ChatStorageStore(),
            ChatEventStore(), ApprovalStore(),
        ):
            satellite.delete_for_chat(identity)

        # Chat-owned uploads die with the chat (bytes included, via the
        # files domain's own delete). Referenced platform files — file
        # parts pointing at existing resources — are untouched.
        attachments = self.files.store.list_visible(user, keys={
            "chat_id": chat_id, "attachment_mode": "uploaded_for_chat",
        })
        for attachment in attachments:
            self.files.delete(
                {"data": {"resource_ref": attachment["resource_ref"]}}, user,
            )

        self.chats.delete_chat(chat_id)
