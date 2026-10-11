"""The backend's session surface, in memory.

Implements the services contract ai_runtime/chat/session.py documents —
state, messages, events, approvals, storage, skills, memories, plans —
as the backend does over its gateway, minus durability. Approvals can be decided by
a test's hook, held open for an explicit resolve, or left forever (a
person who never answers is a case, not a bug).
"""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any, Callable, Dict, List, Optional

from contracts.chat import ACTORS, event_error, part_error

from sim.resources import InMemoryResourceProvider
from sim.schedules import MemoryScheduleStore


class SimSessionServices:
    #: The sim's standing permission: every function of every agent the
    #: host serves. Said outright — a contract with no grants is one
    #: that allows nothing, here as on the platform.
    GRANTS = [{"effect": "allow", "functions": ["*.*.*"]}]

    def __init__(self, decider: Optional[Callable] = None):
        #: every chat's agents work on one set of records here
        self.provider = InMemoryResourceProvider()
        #: the clock's rows — a schedule is user state, so the services
        #: own where it lives.
        self.schedules = MemoryScheduleStore()
        #: async (request) -> an answer | None — the person's answer to
        #: a card, given at once as the card opens (a decision for an
        #: approval, words or files for a question), or None to hold
        #: the card open for the door's frame.
        self.decider = decider
        #: (request) -> the answer the platform gives a code card by
        #: itself, as the organization's Safety setting would, or '' to
        #: show the card. None: every card is shown.
        self.settler: Optional[Callable] = None

        self.states: Dict[str, dict] = {}
        self.messages: Dict[str, List[dict]] = {}
        self.inbox: Dict[str, List[dict]] = {}
        self.events: List[dict] = []
        #: socket-only emissions (a screen's frames), delivered and not
        #: recorded
        self.relayed: List[dict] = []
        #: which chat each relayed frame was addressed to, in order
        self.relayed_to: List[str] = []
        self.approvals: Dict[str, dict] = {}
        #: the sim's vault of site logins, keyed (host, account)
        self.logins: Dict[tuple, dict] = {}
        self.credential_uses: List[tuple] = []
        self.storage: Dict[str, dict] = {}
        #: the trail: every execution the runtime recorded, in order
        self.audit: List[dict] = []
        self.memories: Dict[str, List[str]] = {}
        self.plans: Dict[str, list] = {}
        self.titles: Dict[str, str] = {}
        self.titles_by_person: Dict[str, bool] = {}
        #: agent ref -> the runtime's last word on its readiness, and
        #: every word in order (host._report is once per process)
        #: The package door (agent-code.md): what is approved anywhere
        #: (``{package_digest, manifest_hash, name}`` rows), the bytes
        #: by digest, what a runtime said of each, and what it pulled.
        self.packages: list = []
        self.archives: Dict[str, bytes] = {}
        self.prepared_packages: Dict[str, dict] = {}
        self.pulls_by_digest: list = []
        self.skills_rows: List[dict] = []
        self.skill_bodies: Dict[str, dict] = {}
        #: per-chat contract overrides — a test (or a dev config) sets
        #: only what it means to change.
        self.contracts: Dict[str, dict] = {}
        self._ids = itertools.count(1)

    # -- the contract ----------------------------------------------------
    async def contract(self, chat_id: str) -> dict:
        """What this chat may do (docs/reference/session-door.md). The sim's
        defaults are the permissive ones — every agent the host serves,
        every function allowed, no model until one is configured."""
        answer = {
            "agents": None, "grants": list(self.GRANTS), "chat_level": 1,
            "llm": None, "max_beats": None, "max_skills": None,
            "skills": None, "routing": None,
            "timezone": "", "safety": {},
        }
        answer.update(self.contracts.get(chat_id) or {})
        return answer

    # -- what only a platform over a network has -------------------------
    def grant(self, chat_id: str, credential: str) -> None:
        """A key to the platform for one chat. This platform is in the
        same process, and needs none."""

    def resources(self, chat_id: str) -> InMemoryResourceProvider:
        """An agent's data, files and secrets, as the chat: one store
        here, whichever chat asks."""
        return self.provider

    # -- the clock's rows -------------------------------------------------
    async def load_schedules(self) -> List[Dict[str, Any]]:
        return await self.schedules.load_schedules()

    async def load_schedules_for(self, chat_id: str) -> List[Dict[str, Any]]:
        return await self.schedules.load_schedules_for(chat_id)

    async def add_schedule(self, row: Dict[str, Any]) -> None:
        await self.schedules.add_schedule(row)

    async def schedule_ran(self, row: Dict[str, Any]) -> bool:
        return await self.schedules.schedule_ran(row)

    async def remove_schedule(self, chat_id: str, schedule_id: str) -> None:
        await self.schedules.remove_schedule(chat_id, schedule_id)

    async def mcp_use(self, chat_id: str, ref: str) -> dict:
        raise RuntimeError("these services keep no MCP servers")

    # -- the package door: what a runtime asks as itself -----------------
    async def approved_packages(self) -> list:
        return [dict(row) for row in self.packages]

    async def fetch_by_digest(self, digest: str) -> bytes:
        self.pulls_by_digest.append(digest)
        archive = self.archives.get(digest)
        if archive is None:
            raise RuntimeError(f"no package {digest[:19]}… is stored here")
        return archive

    async def package_prepared(self, digest: str, state: str, error: str = "",
                               confined: Optional[dict] = None) -> bool:
        self.prepared_packages[digest] = {"state": state, "error": error,
                                          "confined": confined}
        return any(row.get("package_digest") == digest for row in self.packages)

    # -- state -----------------------------------------------------------
    async def load_state(self, chat_id: str) -> Optional[dict]:
        return self.states.get(chat_id)

    #: The platform's own limits (backend/api/endpoints/app/ai): what
    #: it refuses, this refuses, or a test passes here against
    #: something a deployment would turn away.
    STATE_MAX_BYTES = 512 * 1024
    EVENT_MAX_BYTES = 16384
    HISTORY_LIMIT = 100
    ACTORS = ACTORS

    async def save_state(self, chat_id: str, state: dict) -> None:
        written = json.dumps(state, default=str)
        if len(written.encode("utf-8")) > self.STATE_MAX_BYTES:
            raise ValueError(
                f"The state is {len(written.encode('utf-8'))} bytes; the "
                f"limit is {self.STATE_MAX_BYTES}.")
        # A copy, as a store keeps one: what the mind does to its own
        # state after this is not yet saved.
        self.states[chat_id] = json.loads(written)

    # -- messages --------------------------------------------------------
    async def persist_message(self, chat_id: str, actor: str, text: str,
                              parts: list, client_message_id: str = "",
                              source=None):
        """The message and whether this call created it — a client
        message id sent again finds the message it already made."""
        if actor not in self.ACTORS:
            raise ValueError("actor must be user, ai, system, or parent.")
        if not str(text or "") and not parts:
            raise ValueError("parts must be a non-empty list of objects.")
        for part in parts or []:
            problem = part_error(part)
            if problem:
                raise ValueError(f"A part does not fit the chat contract "
                                 f"({problem}): {part}")
        rows = self.messages.setdefault(chat_id, [])
        if client_message_id:
            for existing in rows:
                if existing.get("client_message_id") == client_message_id:
                    return existing, False
        # As the backend: a markdown part must carry words, so a message
        # that is only its data parts travels as exactly those.
        words = ([{"type": "markdown", "content": text,
                   **({"source": source} if source else {})}]
                 if str(text or "") else [])
        message = {
            "message_id": f"msg_{next(self._ids)}",
            "actor": actor,
            "parts": words + list(parts),
            **({"client_message_id": client_message_id}
               if client_message_id else {}),
        }
        rows.append(message)
        return message, True

    async def history(self, chat_id: str) -> List[dict]:
        return list(self.messages.get(chat_id, []))[-self.HISTORY_LIMIT:]

    # -- the inbox: events in, durable before absorbed --------------------
    async def record_event(self, chat_id: str, event: dict) -> int:
        size = len(json.dumps(event, default=str).encode("utf-8"))
        if size > self.EVENT_MAX_BYTES:
            raise ValueError(f"The event is {size} bytes; the limit is "
                             f"{self.EVENT_MAX_BYTES}.")
        rows = self.inbox.setdefault(chat_id, [])
        seq = len(rows) + 1
        rows.append({**event, "seq": seq})
        return seq

    async def events_since(self, chat_id: str, cursor: int) -> List[dict]:
        return [dict(row) for row in self.inbox.get(chat_id, [])
                if row["seq"] > cursor]

    # -- events out ------------------------------------------------------
    async def emit(self, chat_id: str, event: dict) -> int:
        """Recorded, and answered with its sequence — per chat, as the
        backend allocates it. What the backend would refuse, the sim
        refuses louder: an event outside the chat contract fails the
        test that emitted it (contracts/chat.py)."""
        problem = event_error(event)
        if problem:
            raise ValueError(f"Emission does not fit the chat contract "
                             f"({problem}): {event}")
        self.events.append({**event, "chat_id": chat_id})
        return sum(1 for row in self.events if row.get("chat_id") == chat_id)

    # -- cards -----------------------------------------------------------
    async def relay(self, chat_id: str, event: dict) -> None:
        """A frame delivered to the chat's audience and never recorded:
        the host's in production, remembered here for a test to read."""
        self.relayed.append(dict(event))
        self.relayed_to.append(chat_id)

    async def open_card(self, chat_id: str, request: dict) -> dict:
        """A card on the record: ``{approval_id, answer, by}``. The
        platform answers a code card itself where a test's ``settler``
        says the Safety setting would (``by`` is ``platform``); a
        test's ``decider`` answers any card as the person would, at
        once (``by`` is ``person``); else ``answer`` is None and the
        card waits for the door."""
        approval_id = f"apr_{next(self._ids)}"
        record = {"chat_id": chat_id, "request": dict(request),
                  "status": "pending", "answer": None}
        self.approvals[approval_id] = record
        answer, by = None, ""
        if self.settler is not None and request.get("expects") == "code":
            settled = str(self.settler(request) or "")
            if settled:
                answer, by = settled, "platform"
        if answer is None and self.decider is not None:
            decided = await self.decider(request)
            if decided is not None:
                answer, by = decided, "person"
        if answer is not None:
            record["answer"] = answer
            record["status"] = "answered"
        return {"approval_id": approval_id, "answer": answer, "by": by}

    def answered(self, approval_id: str, answer: Any) -> None:
        """What the door delivered for a card, written on its record —
        as the backend writes the person's decision."""
        record = self.approvals.get(approval_id)
        if record is not None and record["status"] == "pending":
            record["answer"] = answer
            record["status"] = "answered"

    async def pending_cards(self, chat_id: str, kind: str = "") -> list:
        return [{"approval_id": approval_id, **record}
                for approval_id, record in self.approvals.items()
                if record.get("chat_id") == chat_id
                and record.get("status") == "pending"
                and (not kind or record["request"].get("kind") == kind)]

    async def expire_card(self, chat_id: str, approval_id: str) -> None:
        record = self.approvals.get(approval_id)
        if record is not None:
            record["status"] = "expired"

    # -- the trail -------------------------------------------------------
    async def record_audit(self, chat_id: str, event: dict) -> None:
        self.audit.append({"chat_id": chat_id, **dict(event)})

    # -- storage ---------------------------------------------------------
    async def store_result(self, chat_id: str, source: str,
                           result: dict) -> str:
        ref = f"stg_{next(self._ids)}"
        self.storage[ref] = {"chat_id": chat_id, "source": source,
                             "data": dict(result)}
        return ref

    async def read_result(self, chat_id: str, storage_ref: str,
                          path: str) -> Any:
        record = self.storage.get(storage_ref)
        if record is None or record["chat_id"] != chat_id:
            raise KeyError(f"Unknown stored result '{storage_ref}'")
        value: Any = record["data"]
        for part in [p for p in str(path or "").split(".") if p]:
            if isinstance(value, list):
                value = value[int(part)]
            elif isinstance(value, dict):
                value = value[part]
            else:
                raise KeyError(f"Path '{path}' does not resolve")
        return value

    # -- skills / memory / plan ------------------------------------------
    async def list_skills(self, chat_id: str) -> List[dict]:
        return list(self.skills_rows)

    async def read_skill(self, chat_id: str, ref: str) -> Optional[dict]:
        return self.skill_bodies.get(ref)

    async def read_image(self, chat_id: str, ref: str) -> dict:
        """A stored file, encoded, the way the backend's download answers
        it. By ref across every category: an attachment belongs to the
        chat, not to an agent."""
        import base64

        for records in self.provider.files.values():
            record = records.get(ref)
            if record is None:
                continue
            raw = record.get("content") or b""
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            return {
                "filename": str(record.get("filename") or ""),
                "file_type": str(record.get("file_type") or ""),
                "file_size": len(raw),
                "content_base64": base64.b64encode(raw).decode("ascii"),
            }
        return {}

    async def resolve_credential(self, chat_id: str, payload: dict) -> dict:
        """The backend's Secrets:Credential:Resolve, over ``self.logins``:
        ``{(host, account): {"values": {...}, "consents": {"agent@site"},
        "resource_ref": ...}}``. A test's decider answers the cards and
        writes here as the person would."""
        host = str(payload.get("host") or "").lower().split("://")[-1].split("/")[0]
        host = ".".join(host.split(".")[-2:])  # the registrable domain, as the backend keys it
        site = str(payload.get("site") or host)
        agent = str(payload.get("agent_ref") or "")
        fields = list(payload.get("fields") or [])
        base = {"host": host, "site": site, "agent_ref": agent,
                "resource_id": f"site__{host.replace('.', '_')}",
                "definition_ref": f"org:site__{host.replace('.', '_')}/v1"}
        rows = [(key, row) for key, row in self.logins.items() if key[0] == host]
        pinned = str(payload.get("resource_ref") or "")
        account = str(payload.get("account") or "").lower()
        if pinned:
            rows = [(k, r) for k, r in rows if r.get("resource_ref") == pinned]
        elif account:
            rows = [(k, r) for k, r in rows if k[1] == account]
        if not rows:
            return {"status": "missing", **base, "account": account,
                    "fields": fields, "existing": False}
        if len(rows) > 1:
            return {"status": "choose", **base, "instances": [
                {"resource_ref": r["resource_ref"], "name": f"{host} — {k[1]}",
                 "account": k[1]} for k, r in rows]}
        (key, row), = rows
        if f"{agent}@{site}" not in row.get("consents", set()):
            return {"status": "consent", **base, "resource_ref": row["resource_ref"],
                    "account": key[1], "fields": fields}
        remembered = [f for f in fields if f.get("remember", True)]
        missing = [f for f in remembered
                   if payload.get("refresh") or (f.get("required", True)
                                                 and not row["values"].get(f["name"]))]
        if missing:
            return {"status": "missing", **base, "resource_ref": row["resource_ref"],
                    "account": key[1], "fields": missing, "existing": True}
        self.credential_uses.append((row["resource_ref"], agent, site))
        return {"status": "ready", **base, "resource_ref": row["resource_ref"],
                "account": key[1], "values": {**row["values"], "host": host,
                                              "account": key[1]},
                "ask": [f for f in fields if not f.get("remember", True)]}

    async def list_files(self, chat_id: str) -> List[dict]:
        """Every stored file, the way the backend lists them: metadata
        under ``values``, whatever category an agent filed it in — the
        person sees their own files, whoever made them."""
        listed = []
        for records in self.provider.files.values():
            for ref, record in records.items():
                raw = record.get("content") or b""
                listed.append({
                    "resource_ref": ref,
                    "keys": {},
                    "values": {
                        "filename": str(record.get("filename") or ""),
                        "file_type": str(record.get("file_type") or ""),
                        "file_size": len(raw),
                        "folder": str(record.get("folder") or ""),
                    },
                    "created_at": str(record.get("created_at") or ""),
                })
        return listed

    async def list_memories(self, chat_id: str) -> List[str]:
        return list(self.memories.get(chat_id, []))

    async def add_memory(self, chat_id: str, text: str) -> dict:
        self.memories.setdefault(chat_id, []).append(text)
        return {"text": text}

    async def save_plan(self, chat_id: str, steps: list) -> None:
        self.plans[chat_id] = list(steps)

    async def title_chat(self, chat_id: str, title: str) -> bool:
        if self.titles_by_person.get(chat_id):
            return False
        self.titles[chat_id] = title
        return True
