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
from typing import Any, Callable, Dict, List, Optional

from contracts.chat import event_error

from sim.resources import InMemoryResourceProvider
from sim.schedules import MemoryScheduleStore


class SimSessionServices:
    #: The sim's standing permission: every function of every agent the
    #: host serves. Said outright — a contract with no grants is one
    #: that allows nothing, here as on the platform.
    GRANTS = [{"effect": "allow", "functions": ["*.*.*"]}]

    def __init__(self, decider: Optional[Callable] = None):
        self.provider = InMemoryResourceProvider()
        #: the clock's rows — a schedule is user state, so the services
        #: own where it lives.
        self.schedules = MemoryScheduleStore()
        #: async (request) -> bool | None — an immediate decision, or
        #: None to hold the approval open for resolve_approval.
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
        self.prepared: Dict[str, dict] = {}
        self.prepared_reports: List[tuple] = []
        self.skills_rows: List[dict] = []
        self.skill_bodies: Dict[str, dict] = {}
        #: per-chat contract overrides — a test (or a dev config) sets
        #: only what it means to change.
        self.contracts: Dict[str, dict] = {}
        #: What the platform still approves, deployment-wide, for the
        #: runtime's disk reclaim. None means "everything you hold" —
        #: a standalone runtime has no approvals to lose, and a sim that
        #: answered an empty set would invite it to delete its own
        #: agents. A test sets it to name what should survive.
        self.pinned: Optional[set] = None
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

    async def fetch_package(self, chat_id: str, agent_id: str) -> dict:
        """The pull door. Nothing is approved here to pull: the agents
        this stand-in serves are the ones its host was started with."""
        raise RuntimeError("these services have no pull door")

    async def mcp_use(self, chat_id: str, ref: str) -> dict:
        raise RuntimeError("these services keep no MCP servers")

    async def pinned_digests(self, chat_id: str) -> Optional[set]:
        """Which packages are still approved anywhere (host.reclaim).

        None until a test says otherwise, and the host reads an empty
        answer as "do not delete anything" — so the sim's default is to
        keep every package, which is what a runtime with no platform
        behind it should do."""
        return self.pinned

    # -- state -----------------------------------------------------------
    async def load_state(self, chat_id: str) -> Optional[dict]:
        return self.states.get(chat_id)

    async def save_state(self, chat_id: str, state: dict) -> None:
        self.states[chat_id] = state

    # -- messages --------------------------------------------------------
    async def persist_message(self, chat_id: str, actor: str, text: str,
                              parts: list, client_message_id: str = "",
                              source=None):
        """The message and whether this call created it — a client
        message id sent again finds the message it already made."""
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
        return list(self.messages.get(chat_id, []))

    # -- the inbox: events in, durable before absorbed --------------------
    async def record_event(self, chat_id: str, event: dict) -> int:
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

    # -- approvals -------------------------------------------------------
    async def relay(self, chat_id: str, event: dict) -> None:
        self.relayed.append(dict(event))
        self.relayed_to.append(chat_id)

    async def open_approval(self, chat_id: str, request: dict) -> str:
        approval_id = f"apr_{next(self._ids)}"
        self.approvals[approval_id] = {
            "chat_id": chat_id, "request": dict(request),
            "future": asyncio.get_running_loop().create_future(),
            "decision": None,
        }
        return approval_id

    async def open_card(self, chat_id: str, request: dict) -> dict:
        """A question card opened, and whether the platform settled it
        by itself (the backend's AI:Approval:Open, for code)."""
        approval_id = await self.open_approval(chat_id, request)
        settled = ""
        if self.settler is not None and request.get("expects") == "code":
            settled = str(self.settler(request) or "")
        if settled:
            self.approvals[approval_id]["answer"] = settled
            self.approvals[approval_id]["settled"] = True
        return {"approval_id": approval_id, "settled": settled}

    async def wait_approval(self, approval_id: str) -> bool:
        record = self.approvals[approval_id]
        if self.decider is not None:
            decision = await self.decider(record["request"])
            if decision is not None:
                record["decision"] = bool(decision)
                return bool(decision)
        decision = await record["future"]
        record["decision"] = bool(decision)
        return bool(decision)

    async def resolve_approval(self, approval_id: str,
                               decision: bool) -> bool:
        record = self.approvals.get(approval_id)
        if record is None or record["future"].done():
            return False
        record["future"].set_result(bool(decision))
        return True

    async def wait_answer(self, approval_id: str):
        """A question's answer — the decider's, when one is set and
        answers, else whatever resolve_answer delivers."""
        record = self.approvals[approval_id]
        if self.decider is not None:
            answer = await self.decider(record["request"])
            if answer is not None:
                record["answer"] = answer
                return answer
        answer = await record["future"]
        record["answer"] = answer
        return answer

    async def resolve_answer(self, approval_id: str, answer: str) -> bool:
        record = self.approvals.get(approval_id)
        if record is None or record["future"].done():
            return False
        record["future"].set_result(answer)
        return True

    async def pending_questions(self, chat_id: str) -> list:
        return [
            {"approval_id": approval_id, "kind": "question",
             "request": dict(record["request"])}
            for approval_id, record in self.approvals.items()
            if record["chat_id"] == chat_id
            and record["request"].get("kind") == "question"
            and record.get("status") != "expired"
            and not record["future"].done()
        ]

    async def pending_cards(self, chat_id: str) -> list:
        return [{"approval_id": approval_id, **record}
                for approval_id, record in self.approvals.items()
                if record.get("chat_id") == chat_id
                and record.get("status", "pending") == "pending"]

    async def expire_approval(self, chat_id: str, approval_id: str) -> None:
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
    async def list_skills(self) -> List[dict]:
        return list(self.skills_rows)

    async def read_skill(self, ref: str) -> Optional[dict]:
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

    async def agent_prepared(self, chat_id: str, agent_id: str, digest: str,
                             state: str, error: str = "",
                             confined: Optional[dict] = None) -> bool:
        self.prepared[agent_id] = {"digest": digest, "state": state, "error": error,
                                   "confined": confined}
        self.prepared_reports.append((chat_id, agent_id, digest, state))
        return True

    async def title_chat(self, chat_id: str, title: str) -> bool:
        if self.titles_by_person.get(chat_id):
            return False
        self.titles[chat_id] = title
        return True
