"""The platform, reached over the backend's /app gateway
(docs/system/chat-session.md, docs/reference/session-door.md).

One object for the host, holding one credential per chat: the dial
hands it over (a key, never authority), and every call names the chat
it acts for — a chat, or a sub-assistant's thread under it — and
carries that chat's credential. What a chat may do is still only what
``contract`` answers. The sim (sim/session_services.py) is the same
contract in memory; nothing that reasons can tell them apart.

    backend = Backend(Gateway(url), RuntimeIdentity(private_key))
    backend.grant(chat_id, credential)          at the dial
    await backend.contract(chat_id)             what the chat may do
    await backend.persist_message(chat_id, …)   the record
    backend.resources(chat_id)                  an agent's data, files and
                                                secrets, as the chat (resources.py)
    await backend.open_card(chat_id, request)   a card on the record
    await backend.approved_packages()           as the runtime itself: the
                                                package door (agent-code.md)

One thing the runtime asks as itself and not as any chat: which
packages are approved anywhere, their bytes, and where to say one is
ready — the package door, with a token only a runtime can sign
(``RuntimeIdentity``, the mirror of the backend's own service token).
The key is required at start (settings.py): a runtime has no other way
to code.

Two things the sim does in memory that a network makes explicit:

- **A child's records** are addressed as ``<chat>/<thread>`` by the
  runtime and as ``chat_id`` + ``thread`` by the backend (child
  threads); the split happens here. An agent's resources are the
  conversation's, never a thread's.
- **Waiting on a card** is not a request the backend holds open: the
  answer arrives as the door's ``approval_decided`` or
  ``question_answered`` frame, and the chat's cards (chat/cards.py)
  settle their own waiter.
"""

from __future__ import annotations

import base64
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
import jwt

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
        try:
            response = await self._client.post(
                "/app", json=body, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            # A timeout, a refused connection: said as every failure of
            # the platform is, with what happened where its own text is
            # empty (a read timeout's is).
            said = str(exc).strip()
            raise GatewayError(
                f"{endpoint} could not reach the platform: "
                f"{type(exc).__name__}" + (f": {said[:300]}" if said else ""))
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
                said = error.get("message") if isinstance(error, dict) else error
                raise GatewayError(str(said or f"{endpoint} failed"))
            return payload.get("data") or {}
        return payload if isinstance(payload, dict) else {}

    async def close(self) -> None:
        await self._client.aclose()


class RuntimeIdentity:
    """The runtime's own identity toward the backend: a short-lived
    statement that *this is a runtime*, signed with a key only runtimes
    hold (AI_RUNTIME_SERVICE_PRIVATE_KEY; the backend verifies with the
    public half). It names no person and no organization, and the
    backend lets it through the package door and nowhere else
    (backend/server/authentication/access.py). The mirror of the token
    the backend proves itself with on a dial."""

    ALGORITHM = "RS256"
    ISSUER = "decentai-ai-runtime"
    AUDIENCE = "decentai-backend"
    TTL_SECONDS = 60
    KEY_ID = "ai-runtime-service-v1"

    def __init__(self, private_key: str):
        self.private_key = private_key

    def token(self) -> str:
        now = datetime.now(timezone.utc)
        return jwt.encode({
            "typ": "runtime_service",
            "iss": self.ISSUER, "sub": self.ISSUER, "aud": self.AUDIENCE,
            "jti": f"rst_{uuid.uuid4().hex}",
            "iat": now, "nbf": now,
            "exp": now + timedelta(seconds=self.TTL_SECONDS),
        }, self.private_key, algorithm=self.ALGORITHM,
            headers={"kid": self.KEY_ID})


class Backend:
    def __init__(self, gateway, identity: Optional[RuntimeIdentity] = None):
        self.gateway = gateway
        #: chat_id -> the delegation to call as, granted by the dial
        self.credentials: Dict[str, str] = {}
        #: What this runtime is as itself. None only in a test that
        #: asks nothing of the package door.
        self.identity = identity
        self.logger = logging.getLogger(self.__class__.__name__)

    # -- as itself: the package door ------------------------------------
    async def _as_runtime(self, endpoint: str, data: dict) -> dict:
        if self.identity is None:
            raise GatewayError(
                f"{endpoint}: this runtime has no identity of its own "
                f"(AI_RUNTIME_SERVICE_PRIVATE_KEY is not set)", 401)
        return await self.gateway.call(endpoint, data, self.identity.token())

    async def approved_packages(self) -> List[dict]:
        """Every package some standing approval of any organization
        points at: ``{package_digest, manifest_hash, name}`` each — what
        this runtime stocks itself with, and keeps when it reclaims."""
        answer = await self._as_runtime("Agents:Package:List", {})
        return [dict(row) for row in (answer.get("packages") or [])
                if isinstance(row, dict)]

    async def fetch_by_digest(self, digest: str) -> bytes:
        """The bytes a digest names, whichever organization stored them.
        The library hashes them before writing: the name is the proof."""
        answer = await self._as_runtime(
            "Agents:Package:Fetch", {"package_digest": digest})
        encoded = str((answer or {}).get("package") or "")
        if not encoded:
            raise GatewayError(f"the package door answered with no package for "
                               f"{str(digest)[:19]}…")
        return base64.b64decode(encoded)

    async def package_prepared(self, digest: str, state: str, error: str = "",
                               confined: Optional[dict] = None) -> bool:
        """This runtime's word that a package is ready here — or would
        not build — heard by every approval that pins the digest, and
        with it what this runtime holds the agent to. False when nobody
        pins that digest any more."""
        payload: Dict[str, Any] = {"package_digest": digest, "state": state,
                                   "error": error}
        if confined is not None:
            payload["confined"] = dict(confined)
        answer = await self._as_runtime("Agents:Package:Prepared", payload)
        return bool((answer or {}).get("recorded"))

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

    # -- the contract ----------------------------------------------------
    async def contract(self, chat_id: str) -> dict:
        answer = await self.call(chat_id, "AI:Chat:Contract")
        llm = answer.get("llm")
        if isinstance(llm, dict) and llm.get("secret_ref"):
            # The block names a connection; the key and its settings are
            # the settings domain's, resolved for the delegating person.
            llm = self._with_connection(llm, await self.call(
                chat_id, "Settings:Llm:Use", {"connection_id": llm["secret_ref"]}))
        routing = answer.get("routing") if isinstance(answer.get("routing"), dict) else None
        if routing is not None and isinstance(routing.get("embedding"), dict) \
                and routing["embedding"].get("secret_ref"):
            # The embedding model's key, at the settings door, the way
            # the chat's model key is fetched; a door that refuses
            # leaves routing off for this chat rather than the chat.
            try:
                routing["embedding"] = self._with_connection(
                    routing["embedding"], await self.call(
                        chat_id, "Settings:Llm:Use", {
                            "connection_id": routing["embedding"]["secret_ref"]}))
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

    @staticmethod
    def _with_connection(block: dict, resolved: dict) -> dict:
        """A model block, with the connection it names put in.

        The provider, the address and the key are the connection's and
        win over whatever the block says: they are whose key it is and
        where it is sent. The model is the block's — a connection is one
        key to every model its provider serves — and the one the
        connection starts with only when the block names none."""
        keys = resolved.get("keys") or {}
        return {**block, **keys,
                "model": block.get("model") or keys.get("model") or "",
                **(resolved.get("values") or {})}

    # -- the pull door (docs/system/agent-code.md) --------------------------
    async def mcp_use(self, chat_id: str, ref: str) -> dict:
        """Where one of the person's MCP servers is and what it is
        reached with, as this chat's delegation: ``{url, headers}``.
        Asked when a tool is called, never kept."""
        return await self.call(chat_id, "Mcp:Server:Use", {"resource_ref": ref})

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

    # -- cards -----------------------------------------------------------
    async def open_card(self, chat_id: str, request: dict) -> dict:
        """A card opened on the record — an approval, a question, code,
        a login, files: ``{approval_id, answer, by}``. ``answer`` is
        None where a person is to be asked, and the platform's own
        answer where the person's Safety setting let the card through
        without showing it (code, and only code); ``by`` then says
        ``platform``. The person's answer arrives at the door later,
        and the chat's cards settle their own waiter."""
        answer = await self.call(chat_id, "AI:Approval:Open",
                                 {"request": request})
        settled = str(answer.get("settled") or "")
        return {"approval_id": str(answer.get("approval_id") or ""),
                "answer": settled or None,
                "by": "platform" if settled else ""}

    async def pending_cards(self, chat_id: str, kind: str = "") -> list:
        """Every card still open on this chat's record — of one kind,
        where ``kind`` names it. What a kill expires; what a session
        opening closes as a dead process's."""
        try:
            answer = await self.call(chat_id, "AI:Approval:List", {})
        except Exception as exc:
            self.logger.warning(f"Pending cards of {chat_id} unread: {exc}")
            return []
        return [card for card in (answer or {}).get("approvals") or []
                if isinstance(card, dict) and (not kind or card.get("kind") == kind)]

    async def expire_card(self, chat_id: str, approval_id: str) -> None:
        """A card nobody answered in time, or nobody will hear the
        answer to: closed on the record, so a late answer is refused
        rather than lost."""
        try:
            await self.call(chat_id, "AI:Approval:Expire",
                            {"approval_id": approval_id})
        except Exception as exc:
            self.logger.warning(f"Card {approval_id} not expired: {exc}")

    # -- storage and the trail ---------------------------------------------
    async def record_audit(self, chat_id: str, event: dict) -> None:
        """One execution on the trail. A witness, never a gate: a
        record that will not write is logged and the work stands."""
        try:
            await self.call(chat_id, "AI:Audit:Record", {"event": event})
        except Exception as exc:
            self.logger.warning(f"Execution not audited: {exc}")

    async def record_usage(self, chat_id: str, calls: list) -> None:
        """A batch of the chat's model calls — what each was for, the
        model, the tokens it used, how long it took — for the
        organization's monitoring (docs/system/monitoring.md). The
        caller (llms/meter.py) logs a refusal and lets the batch go."""
        await self.call(chat_id, "AI:Usage:Record", {"calls": list(calls)})

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
    async def list_skills(self, chat_id: str) -> List[dict]:
        answer = await self.call(chat_id, "Skills:Skill:List", {})
        return [{
            "ref": row.get("resource_ref"),
            "title": (row.get("keys") or {}).get("title"),
            "summary": (row.get("keys") or {}).get("summary"),
        } for row in answer.get("resources") or []]

    async def read_skill(self, chat_id: str, ref: str) -> Optional[dict]:
        answer = await self.call(chat_id, "Skills:Skill:Get", {"resource_ref": ref})
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

    # -- an agent's resources, as the chat ----------------------------------
    def resources(self, chat_id: str) -> "ChatResources":
        """What an agent's functions read and write (execution/resources.py),
        as this chat's delegation. The conversation's, never a thread's:
        a helper's agents work on the same records as the chat's."""
        return ChatResources(self, self._split(chat_id)[0])

    # -- the clock's rows -------------------------------------------------
    # Kept by the platform per chat. The clock reads every chat this
    # process holds a credential for, and writes one row at a time as
    # the chat the row belongs to; a chat's rows become loadable when
    # its credential arrives (``load_schedules_for``, which the host
    # feeds to the clock).

    async def load_schedules_for(self, chat_id: str) -> List[Dict[str, Any]]:
        answer = await self.call(chat_id, "AI:Schedule:Load")
        return list(answer.get("rows") or [])

    async def load_schedules(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for chat_id in list(self.credentials):
            try:
                rows.extend(await self.load_schedules_for(chat_id))
            except Exception as exc:
                self.logger.warning(f"Schedules for {chat_id} not loaded: {exc}")
        return rows

    async def add_schedule(self, row: Dict[str, Any]) -> None:
        await self._write_row(row.get("chat_id"), "AI:Schedule:Add", {"row": row})

    async def schedule_ran(self, row: Dict[str, Any]) -> bool:
        """False when the platform no longer keeps the row — the person
        deleted it."""
        answer = await self._write_row(
            row.get("chat_id"), "AI:Schedule:Ran", {"row": row})
        return not answer.get("gone")

    async def remove_schedule(self, chat_id: str, schedule_id: str) -> None:
        await self._write_row(
            chat_id, "AI:Schedule:Remove", {"schedule_id": schedule_id})

    async def _write_row(self, chat_id, endpoint: str, data: dict) -> dict:
        """One write, as the chat the row belongs to. A key the
        platform no longer accepts is dropped: nothing can be written
        as that chat until its next dial brings a new one."""
        chat_id = str(chat_id or "")
        sent_with = self.credentials.get(chat_id)
        try:
            return await self.call(chat_id, endpoint, data)
        except GatewayError as exc:
            # Only the key this write was sent with. One renewed while
            # the write was on its way is a different key, which nothing
            # has refused.
            if exc.status == 401 and self.credentials.get(chat_id) == sent_with:
                self.revoke(chat_id)
            raise


class ChatResources:
    """An agent's data, files and secrets, as one chat's delegation
    over the gateway. Every operation is made as the person — the
    backend enforces visibility, ownership and encryption; this only
    puts the provider protocol (sim/resources.py is the in-process
    reference) onto the gateway's endpoints."""

    def __init__(self, backend: Backend, chat_id: str):
        self.backend = backend
        self.chat_id = chat_id

    async def _call(self, endpoint: str, data: dict) -> dict:
        return await self.backend.call(self.chat_id, endpoint, data)

    # -- secrets ---------------------------------------------------------
    async def use_secret(self, resource_id: str,
                         ref: Optional[str] = None) -> Dict[str, Any]:
        """By canonical category — the backend resolves the bound
        instance — or by a ref among the category's instances, which the
        backend checks belongs to this slot. Keys and values arrive
        merged into one flat map: the definition decides which fields
        are encrypted, and the function needs the whole credential
        either way."""
        payload: Dict[str, Any] = {"resource_id": resource_id}
        if ref:
            payload["resource_ref"] = ref
        result = await self._call("Secrets:Secret:Use", payload)
        return {**(result.get("keys") or {}), **(result.get("values") or {})}

    async def list_secrets(self, resource_id: str) -> List[Dict[str, Any]]:
        """The category's instances this slot may use, plain half only."""
        result = await self._call("Secrets:Secret:Instances",
                                  {"resource_id": resource_id})
        return list(result.get("instances") or [])

    # -- data ------------------------------------------------------------
    async def list_data(self, resource_id, filters) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"resource_id": resource_id}
        if filters:
            payload["keys"] = dict(filters)
        result = await self._call("Data:Record:List", payload)
        return result.get("resources") or []

    # A call by reference names the slot it is for: the backend answers
    # only a document kept under that slot, so an agent never reaches
    # another agent's records, or the person's own, by guessing a ref.
    # A read with no slot (None) is of a ref the call was handed, and is
    # answered as the person would be (execution/resources.py).

    @staticmethod
    def _by_ref(ref, slot) -> Dict[str, Any]:
        return {"resource_ref": ref, **({"slot": slot} if slot else {})}

    async def read_data(self, resource_id, ref) -> Dict[str, Any]:
        result = await self._call("Data:Record:Get", self._by_ref(ref, resource_id))
        return result.get("resource") or {}

    async def create_data(self, resource_id, keys, values) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "resource_id": resource_id, "keys": dict(keys or {}),
        }
        if values:
            payload["values"] = dict(values)
        result = await self._call("Data:Record:Create", payload)
        return result.get("resource") or {}

    async def update_data(self, resource_id, ref, keys, values) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"resource_ref": ref, "slot": resource_id}
        if keys:
            payload["keys"] = dict(keys)
        if values:
            payload["values"] = dict(values)
        result = await self._call("Data:Record:Update", payload)
        return result.get("resource") or {}

    async def delete_data(self, resource_id, ref) -> bool:
        result = await self._call("Data:Record:Delete",
                                  {"resource_ref": ref, "slot": resource_id})
        return bool(result.get("deleted"))

    # -- files -----------------------------------------------------------
    async def list_files(self, resource_id) -> List[Dict[str, Any]]:
        result = await self._call("Files:File:List",
                                  {"keys": {"category": resource_id}})
        return result.get("resources") or []

    async def read_file(self, resource_id, ref) -> Dict[str, Any]:
        result = await self._call(
            "Files:File:Download",
            {**self._by_ref(ref, resource_id), "encoding": "base64"})
        # The bytes, as bytes: the mediated access decides what a
        # function sees (text when they decode, base64 always). Decoding
        # here with replacement is how a workbook once became holes.
        record = {k: v for k, v in result.items() if k != "content_base64"}
        if result.get("content_base64") is not None:
            record["content"] = base64.b64decode(result["content_base64"])
        return record

    async def create_file(self, resource_id, filename, content) -> Dict[str, Any]:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        result = await self._call("Files:File:Upload", {
            "filename": filename,
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "folder": "agents",
            "meta": {"category": resource_id},
        })
        return result.get("resource") or {}

    async def delete_file(self, resource_id, ref) -> bool:
        result = await self._call("Files:File:Delete",
                                  {"resource_ref": ref, "slot": resource_id})
        return bool(result.get("deleted"))
