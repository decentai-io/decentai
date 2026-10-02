"""The services contract over the backend's /app gateway.

One object for the host, holding one credential per chat: the dial
hands it over (a key, never authority — docs/reference/session-door.md), and
every call for that chat — or for a sub-assistant thread under it —
carries it. What a chat may do is still only what ``contract`` answers.

Two things the sim does in memory that a network makes explicit:

- **A child's records** are addressed as ``<chat>/<thread>`` by the
  runtime and as ``chat_id`` + ``thread`` by the backend
  (docs/system/chat-session.md, child threads); the split happens here.
- **Waiting on an approval** is not a request the backend holds open:
  the decision arrives as the door's ``approval_decided`` frame, and
  the session's ``resolve_approval`` settles the waiter here.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ai_runtime.chat.current import CURRENT_CHAT
from ai_runtime.runtime_logging import RuntimeLoggerFactory
from ai_runtime.services.provider import BackendProvider

#: how much of a chat's conversation frames a fresh mind
HISTORY_LIMIT = 100


class GatewayError(RuntimeError):
    """The platform refused or could not be reached. ``status`` is the
    answer's HTTP status where there was one."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class Gateway:
    """POST /app {endpoint, data} as one credential. The AI domain
    answers in the EndpointResponse envelope; the data-layer and
    settings domains answer plain dicts — both are accepted."""

    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url,
                                         timeout=timeout)

    async def call(self, endpoint: str, data: dict, token: str) -> dict:
        body = {"endpoint": endpoint, "data": {
            "request_id": f"runtime_{secrets.token_urlsafe(18)}",
            **(data or {}),
        }}
        response = await self._client.post(
            "/app", json=body, headers={"Authorization": f"Bearer {token}"})
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code != 200:
            detail = ((payload.get("error") if isinstance(payload, dict)
                       else None) or response.text)
            raise GatewayError(
                f"{endpoint} failed ({response.status_code}): "
                f"{str(detail)[:300]}", response.status_code)
        if isinstance(payload, dict) and "status" in payload \
                and "request_id" in payload:
            if payload.get("status") == "error":
                error = payload.get("error") or {}
                raise GatewayError(
                    str(error.get("message") if isinstance(error, dict)
                        else error) or f"{endpoint} failed")
            return payload.get("data") or {}
        return payload if isinstance(payload, dict) else {}

    async def close(self) -> None:
        await self._client.aclose()


class BackendServices:
    def __init__(self, gateway):
        self.gateway = gateway
        #: chat_id -> the delegation to call as, granted by the dial
        self.credentials: Dict[str, str] = {}
        self._waiters: Dict[str, asyncio.Future] = {}
        self.provider = BackendProvider(self)
        self.schedules = BackendSchedules(self)
        self.logger = RuntimeLoggerFactory.get_logger(self.__class__.__name__)

    # -- credentials -----------------------------------------------------
    def grant(self, chat_id: str, credential: str) -> None:
        self.credentials[self._split(chat_id)[0]] = credential

    def revoke(self, chat_id: str) -> None:
        self.credentials.pop(self._split(chat_id)[0], None)

    @staticmethod
    def _split(chat_id: str) -> Tuple[str, str]:
        """``chat_1/sub_xxxx`` → (chat_1, sub_xxxx); a chat → (chat, "")."""
        root, _, thread = str(chat_id or "").partition("/")
        return root, thread

    async def call(self, chat_id: str, endpoint: str,
                   data: Optional[dict] = None) -> dict:
        root, thread = self._split(chat_id)
        token = self.credentials.get(root)
        if not token:
            raise GatewayError(f"No credential for chat '{root}'.", 401)
        payload = {"chat_id": root, **(data or {})}
        if thread:
            payload["thread"] = thread
        return await self.gateway.call(endpoint, payload, token)

    async def call_current(self, endpoint: str, data: dict) -> dict:
        """As whichever chat the running code acts for — the executor's
        provider and the clock's fires never pass one."""
        chat_id = CURRENT_CHAT.get("")
        if not chat_id:
            raise GatewayError("No chat is current for this call.")
        # Resources are the conversation's, never a thread's.
        return await self.call(self._split(chat_id)[0], endpoint, data)

    # -- the contract ----------------------------------------------------
    async def contract(self, chat_id: str) -> dict:
        answer = await self.call(chat_id, "AI:Chat:Contract")
        llm = answer.get("llm")
        if isinstance(llm, dict) and llm.get("secret_ref"):
            # The block names a connection; the key and its settings are
            # the settings domain's, resolved for the delegating person.
            resolved = await self.call(chat_id, "Settings:Llm:Use", {
                "connection_id": llm["secret_ref"]})
            llm = {**llm, **(resolved.get("keys") or {}),
                   **(resolved.get("values") or {})}
        routing = answer.get("routing") if isinstance(answer.get("routing"), dict) else None
        if routing is not None and isinstance(routing.get("embedding"), dict) \
                and routing["embedding"].get("secret_ref"):
            # The embedding model's key, at the settings door, the way
            # the chat's model key is fetched; a door that refuses
            # leaves routing off for this chat rather than the chat.
            try:
                resolved = await self.call(chat_id, "Settings:Llm:Use", {
                    "connection_id": routing["embedding"]["secret_ref"]})
                routing["embedding"] = {**routing["embedding"],
                                        **(resolved.get("keys") or {}),
                                        **(resolved.get("values") or {})}
            except Exception as exc:
                self.logger.warning(f"Embedding model for {chat_id} not resolved: {exc}")
                routing["embedding"] = None
        return {
            "routing": routing,
            "agents": answer.get("agents"),
            "grants": answer.get("grants"),
            "chat_level": answer.get("chat_level"),
            "llm": llm or None,
            # Why there is no model, in the platform's words: which
            # setting is empty and where to fill it.
            "llm_missing": str(answer.get("llm_missing") or ""),
            # The person's own MCP servers and the tools they kept on.
            "mcp": answer.get("mcp") if isinstance(answer.get("mcp"), list) else [],
            "max_beats": answer.get("max_beats"),
            "max_skills": answer.get("max_skills"),
            "skills": answer.get("skills"),
            "timezone": str(answer.get("timezone") or ""),
            "safety": answer.get("safety") if isinstance(answer.get("safety"), dict) else {},
        }

    # -- the pull door (docs/system/agent-code.md) --------------------------
    async def fetch_package(self, chat_id: str, agent_id: str) -> dict:
        """The approved bytes for one of the organization's agents, as
        this chat's delegation. The approval row decides what bytes the
        name means; the answer carries the package (base64), its digest
        and the manifest hash the installer verifies against."""
        return await self.call(chat_id, "Agents:Agent:Fetch_package",
                               {"agent_id": agent_id})

    async def mcp_use(self, chat_id: str, ref: str) -> dict:
        """Where one of the person's MCP servers is and what it is
        reached with, as this chat's delegation: ``{url, headers}``.
        Asked when a tool is called, never kept."""
        return await self.call(chat_id, "Mcp:Server:Use", {"resource_ref": ref})

    async def pinned_digests(self, chat_id: str) -> set:
        """Every package digest any organization still approves.

        Asked as this chat's delegation because that is the only
        identity a runtime has, and answered deployment-wide because
        this process holds one copy of each package for all of them.
        What comes back is hashes; what it is for is deleting the code
        and environments nobody points at any more."""
        answer = await self.call(chat_id, "Agents:Agent:Pinned_digests")
        return {str(digest) for digest in (answer.get("digests") or [])}

    async def agent_prepared(self, chat_id: str, agent_id: str, digest: str,
                             state: str, error: str = "",
                             confined: Optional[dict] = None) -> bool:
        """This process's word that the agent's code is ready here —
        or would not build — as this chat's delegation, and with it
        what this process holds the agent to. False when the platform
        pins another version by now and kept nothing."""
        payload = {"agent_id": agent_id, "package_digest": digest,
                   "state": state, "error": error}
        if confined is not None:
            payload["confined"] = dict(confined)
        answer = await self.call(chat_id, "Agents:Agent:Prepared", payload)
        return bool((answer or {}).get("recorded"))

    # -- state -----------------------------------------------------------
    async def load_state(self, chat_id: str) -> Optional[dict]:
        answer = await self.call(chat_id, "AI:State:Get")
        state = answer.get("state")
        return state if isinstance(state, dict) else None

    async def save_state(self, chat_id: str, state: dict) -> None:
        await self.call(chat_id, "AI:State:Save", {"state": state})

    # -- messages --------------------------------------------------------
    async def persist_message(self, chat_id: str, actor: str, text: str,
                              parts: list, client_message_id: str = "",
                              source: Optional[dict] = None
                              ) -> Tuple[dict, bool]:
        """The message, and whether this call created it. A client
        message id names the submission: sent again, it finds the
        message it already made rather than making a second."""
        # A markdown part must carry words: the platform refuses an
        # empty one. A message that is only its data parts travels as
        # exactly those.
        words = ([{"type": "markdown", "content": text,
                   **({"source": source} if source else {})}]
                 if str(text or "") else [])
        answer = await self.call(chat_id, "AI:Message:Create", {
            "actor": actor,
            "parts": words + list(parts),
            **({"client_message_id": client_message_id}
               if client_message_id else {}),
        })
        return answer.get("message") or {}, bool(answer.get("created", True))

    async def history(self, chat_id: str) -> List[dict]:
        answer = await self.call(chat_id, "AI:Message:List",
                                 {"limit": HISTORY_LIMIT})
        return list(answer.get("messages") or [])

    async def read_image(self, chat_id: str, ref: str) -> dict:
        """A file the person attached, encoded, for the MIND to look at.

        Deliberately not the executor's mediated read: that one enforces
        an agent's resource grants, because an agent reading a file must
        be checked against what it was approved for. No agent is in this
        picture — it is the person's own upload, in their own chat, read
        so the model can see what they are talking about.

        ``encoding: base64`` answers filename, file_type, file_size and
        content_base64 together, so nothing has to guess a type from a
        name or re-encode bytes it just decoded."""
        return await self.call(chat_id, "Files:File:Download",
                               {"resource_ref": ref, "encoding": "base64"})

    async def resolve_credential(self, chat_id: str, payload: dict) -> dict:
        """A login an agent asks for as it works: what the vault holds
        for this person, and which card is owed. Runtime-only at the
        backend, under the chat's delegation."""
        return await self.call(chat_id, "Secrets:Credential:Resolve", dict(payload))

    async def list_files(self, chat_id: str) -> List[dict]:
        """Every file the person can see, for the assistant's
        find_files. Under the chat's delegation, so the backend's
        visibility rules — theirs, their groups' — decide what is
        listed; no agent and no grant is in this picture."""
        answer = await self.call(chat_id, "Files:File:List", {})
        return list(answer.get("resources") or [])

    # -- the inbox, and events out ---------------------------------------
    async def record_event(self, chat_id: str, event: dict) -> int:
        answer = await self.call(chat_id, "AI:Event:Record", {"event": event})
        return int(answer.get("seq") or 0)

    async def events_since(self, chat_id: str, cursor: int) -> List[dict]:
        answer = await self.call(chat_id, "AI:Event:Since",
                                 {"cursor": int(cursor or 0)})
        return list(answer.get("events") or [])

    async def emit(self, chat_id: str, event: dict) -> Optional[int]:
        """Record one outbound event; the sequence the backend gave it,
        or None when it would not record. Resilience, never authority:
        a narration that will not record is still relayed live by the
        host — without a sequence, since the audience could never
        replay what was never kept."""
        try:
            answer = await self.call(chat_id, "AI:Event:Append",
                                     {"event": event})
        except Exception as exc:
            # Refused by the chat contract, most likely: a bug to see,
            # not weather to shrug at (contracts/chat.py).
            self.logger.error(f"Event not recorded: {exc}")
            return None
        seq = (answer or {}).get("seq")
        return seq if isinstance(seq, int) else None

    # -- approvals -------------------------------------------------------
    async def open_approval(self, chat_id: str, request: dict) -> str:
        answer = await self.call(chat_id, "AI:Approval:Open",
                                 {"request": request})
        approval_id = str(answer.get("approval_id") or "")
        self._waiters[approval_id] = asyncio.get_running_loop().create_future()
        return approval_id

    async def open_card(self, chat_id: str, request: dict) -> dict:
        """A question card opened — and, for code the person's Safety
        setting lets through, already settled by the platform:
        ``{approval_id, settled}``, where ``settled`` is the answer it
        gave, or '' when a person is to be asked."""
        answer = await self.call(chat_id, "AI:Approval:Open",
                                 {"request": request})
        approval_id = str(answer.get("approval_id") or "")
        settled = str(answer.get("settled") or "")
        if not settled:
            self._waiters[approval_id] = asyncio.get_running_loop().create_future()
        return {"approval_id": approval_id, "settled": settled}

    async def wait_approval(self, approval_id: str) -> bool:
        waiter = self._waiters.get(approval_id)
        if waiter is None:
            waiter = asyncio.get_running_loop().create_future()
            self._waiters[approval_id] = waiter
        try:
            return bool(await waiter)
        finally:
            self._waiters.pop(approval_id, None)

    async def resolve_approval(self, approval_id: str,
                               decision: bool) -> bool:
        waiter = self._waiters.get(approval_id)
        if waiter is None or waiter.done():
            return False
        waiter.set_result(bool(decision))
        return True

    async def wait_answer(self, approval_id: str):
        """A question's answer, as the question_answered frame brings it
        — the same waiter a decision uses, carrying words, not a yes."""
        waiter = self._waiters.get(approval_id)
        if waiter is None:
            waiter = asyncio.get_running_loop().create_future()
            self._waiters[approval_id] = waiter
        try:
            return await waiter
        finally:
            self._waiters.pop(approval_id, None)

    async def resolve_answer(self, approval_id: str, answer: Any) -> bool:
        """Words, or — a files question — the chosen files as a list."""
        waiter = self._waiters.get(approval_id)
        if waiter is None or waiter.done():
            return False
        waiter.set_result(answer if isinstance(answer, (list, dict)) else str(answer))
        return True

    async def pending_questions(self, chat_id: str) -> list:
        """The question cards still open on this chat's record."""
        try:
            answer = await self.call(chat_id, "AI:Approval:List", {})
        except Exception as exc:
            self.logger.warning(f"Pending cards of {chat_id} unread: {exc}")
            return []
        return [card for card in (answer or {}).get("approvals") or []
                if isinstance(card, dict) and card.get("kind") == "question"]

    async def pending_cards(self, chat_id: str) -> list:
        """Every card still open on this chat's record, whatever its
        kind — what a kill expires."""
        try:
            answer = await self.call(chat_id, "AI:Approval:List", {})
        except Exception as exc:
            self.logger.warning(f"Pending cards of {chat_id} unread: {exc}")
            return []
        return [card for card in (answer or {}).get("approvals") or []
                if isinstance(card, dict)]

    async def expire_approval(self, chat_id: str, approval_id: str) -> None:
        """A question nobody answered in time: closed on the record, so
        a late answer is refused rather than lost."""
        try:
            await self.call(chat_id, "AI:Approval:Expire",
                            {"approval_id": approval_id})
        except Exception as exc:
            self.logger.warning(f"Question {approval_id} not expired: {exc}")

    # -- storage ---------------------------------------------------------
    async def record_audit(self, chat_id: str, event: dict) -> None:
        """One execution on the trail. A witness, never a gate: a
        record that will not write is logged and the work stands."""
        try:
            await self.call(chat_id, "AI:Audit:Record", {"event": event})
        except Exception as exc:
            self.logger.warning(f"Execution not audited: {exc}")

    async def store_result(self, chat_id: str, source: str,
                           result: dict) -> str:
        answer = await self.call(chat_id, "AI:Storage:Create",
                                 {"source": source, "data": result})
        return str((answer.get("storage") or {}).get("storage_ref") or "")

    async def read_result(self, chat_id: str, storage_ref: str,
                          path: str) -> Any:
        answer = await self.call(chat_id, "AI:Storage:Get",
                                 {"storage_ref": storage_ref, "path": path})
        if "value" in answer:
            return answer["value"]
        return (answer.get("storage") or {}).get("data")

    # -- skills, memories, plan ------------------------------------------
    async def list_skills(self) -> List[dict]:
        answer = await self.call_current("Skills:Skill:List", {})
        return [{
            "ref": row.get("resource_ref"),
            "title": (row.get("keys") or {}).get("title"),
            "summary": (row.get("keys") or {}).get("summary"),
        } for row in answer.get("resources") or []]

    async def read_skill(self, ref: str) -> Optional[dict]:
        answer = await self.call_current("Skills:Skill:Get",
                                         {"resource_ref": ref})
        resource = answer.get("resource")
        if not isinstance(resource, dict):
            return None
        return {**(resource.get("keys") or {}),
                **(resource.get("values") or {})}

    async def list_memories(self, chat_id: str) -> List[str]:
        answer = await self.call(chat_id, "Settings:Memory:List")
        return [str(m.get("text") or "") for m in answer.get("memories") or []
                if isinstance(m, dict)]

    async def add_memory(self, chat_id: str, text: str) -> dict:
        answer = await self.call(chat_id, "Settings:Memory:Create", {"text": text})
        return answer.get("memory") or {"text": text}

    async def save_plan(self, chat_id: str, steps: list) -> None:
        await self.call(chat_id, "AI:Chat:Plan", {"steps": list(steps)})

    async def title_chat(self, chat_id: str, title: str) -> bool:
        """The chat's name from its content; False when the person's
        own name stands."""
        answer = await self.call(chat_id, "AI:Chat:Title", {"title": title})
        return bool((answer or {}).get("kept"))


class BackendSchedules:
    """The clock's rows, kept by the platform per chat. The clock reads
    every chat this process holds a credential for, and writes one row
    at a time as the chat the row belongs to. Rows for a chat become
    loadable when its credential arrives (``load_for``, which the host
    feeds to the clock's ``adopt``)."""

    def __init__(self, services: BackendServices):
        self.services = services

    async def load_for(self, chat_id: str) -> List[Dict[str, Any]]:
        answer = await self.services.call(chat_id, "AI:Schedule:Load")
        return list(answer.get("rows") or [])

    async def load(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for chat_id in list(self.services.credentials):
            try:
                rows.extend(await self.load_for(chat_id))
            except Exception as exc:
                self.services.logger.warning(
                    f"Schedules for {chat_id} not loaded: {exc}")
        return rows

    async def add(self, row: Dict[str, Any]) -> None:
        await self._as(row.get("chat_id"), "AI:Schedule:Add", {"row": row})

    async def ran(self, row: Dict[str, Any]) -> bool:
        """False when the platform no longer keeps the row — the person
        deleted it."""
        answer = await self._as(
            row.get("chat_id"), "AI:Schedule:Ran", {"row": row})
        return not answer.get("gone")

    async def remove(self, chat_id: str, schedule_id: str) -> None:
        await self._as(
            chat_id, "AI:Schedule:Remove", {"schedule_id": schedule_id})

    async def _as(self, chat_id, endpoint: str, data: dict) -> dict:
        """One write, as the chat the row belongs to. A key the
        platform no longer accepts is dropped: nothing can be written
        as that chat until its next dial brings a new one."""
        chat_id = str(chat_id or "")
        try:
            return await self.services.call(chat_id, endpoint, data)
        except GatewayError as exc:
            if exc.status == 401:
                self.services.revoke(chat_id)
            raise
