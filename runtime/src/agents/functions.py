"""The runtime's functions a program calls within a call: record.*, model, chat and network.open, each through the
gate (runtime.md §6; agents.md §6, §9)."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from shared.file_types import FileTypes
from ..api.client import Refused
from ..models.base import Request
from .process import Call, CallFailed, Process

log = logging.getLogger("runtime")

LEVELS = {"record.list": 0, "record.read": 0, "record.create": 1, "record.update": 1, "record.delete": 2, "model": 0,
          "chat": 0, "network.open": 3}
CODES = {400: "invalid", 403: "refused", 404: "not_found", 409: "refused", 413: "invalid", 429: "failed"}


class Functions:
    CHATS_MOST, PICTURES = 5, ("image/png", "image/jpeg", "image/gif", "image/webp")

    def __init__(self, chat: Any):
        self.chat = chat

    async def serve(self, process: Process, message: dict[str, Any]) -> None:
        """One of the program's calls, answered with its result or its error."""
        data, message_id = message["body"].get("data") or {}, message["_id"]
        try:
            call = process.calls.get(str(data.get("within") or ""))
            if call is None:
                raise CallFailed("refused", "A call is made within a call of the runtime's that is in flight.")
            name, inputs = str(data.get("function") or ""), data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
            if name not in LEVELS:
                raise CallFailed("not_found", f"The runtime has no function {name}.")
            handler = {"model": self.model, "chat": self.say, "network.open": self.open}.get(name, self.record)
            output, files = await handler(process, call, name, inputs, message)
            await process.answer(message_id, output, files=files)
        except CallFailed as failed:
            await process.answer(message_id, failed=failed)
        except Refused as refused:
            await process.answer(message_id, failed=CallFailed(CODES.get(refused.status, "failed"), refused.message))
        except asyncio.CancelledError:
            await process.answer(message_id, failed=CallFailed("cancelled", "Cancelled."))
        except Exception as error:
            log.warning(f"{process.name}: a call to the runtime failed: {error!r}")
            await process.answer(message_id, failed=CallFailed("failed", str(error)[:500] or "It failed."))
        finally:
            process.serving.pop(message_id, None)

    async def record(self, process: Process, call: Call, name: str, inputs: dict, message: dict) -> tuple[Any, list]:
        """Asked of the API server, naming the chat, the agent, the function and the call."""
        operation, resource = name.split(".")[1], str(inputs.get("resource") or "")
        await self.chat.gate.calls(process, call, f"{name}:{resource}", LEVELS[name], str(inputs.get("ref") or ""))
        asked = {**inputs, "chat_id": self.chat.id, "agent": process.agent_id, "function": call.function,
                 "call": call.id}
        if operation == "create" and message.get("files"):
            asked["file_id"], _ = await process.kept(message["files"][0]["path"], message["files"][:1])
        try:
            answer = await self.chat.api.call(f"Records:Record:{operation}", asked)
        except Refused as refused:
            if not refused.extra.get("approval"):
                raise
            await self.chat.gate.approved(process.agent_id, call, {
                "function": call.function, "call": f"{name}:{resource}", "record": str(inputs.get("ref") or "")})
            answer = await self.chat.api.call(f"Records:Record:{operation}", asked)
        if answer.get("waiting"):
            filled = await self.chat.asked_card(answer.pop("waiting"), call) or {}
            if filled.get("once"):
                answer["once"] = filled["once"]
        if operation == "read" and "path" not in answer and "size" in answer:
            offered = await process.offer(answer["ref"])
            return {**answer, "path": f"in/{offered['id']}/{offered['name']}"}, [offered]
        if operation == "read":
            self._revealed(process, resource, answer.get("fields") or {})
        return answer, []

    async def model(self, process: Process, call: Call, name: str, inputs: dict, message: dict) -> tuple[Any, list]:
        """The chat's model connection, with the pictures the program named."""
        await self.chat.gate.calls(process, call, "model", 0)
        messages = inputs.get("messages") if isinstance(inputs.get("messages"), list) else []
        system = "\n\n".join(str(m.get("text") or "") for m in messages if isinstance(m, dict) and m.get("role") == "system")
        spoken = [{"role": "assistant" if m.get("role") == "assistant" else "user", "text": str(m.get("text") or "")}
                  for m in messages if isinstance(m, dict) and m.get("role") != "system"]
        if not spoken:
            raise CallFailed("invalid", "messages holds at least one message that isn't the system's.")
        pictures = []
        for file in (message.get("files") or [])[:16]:
            data = await self.chat.sandboxes.pulled(process.sandbox, file["id"], 5 * 1024 * 1024)
            kind = FileTypes.of(file["path"])
            if kind not in self.PICTURES or not FileTypes.agrees(kind, data[:16]):
                raise CallFailed("invalid", f"{file['path']} is not a PNG, JPEG, GIF or WebP picture by its bytes.")
            pictures.append({"mime": kind, "data": base64.b64encode(data).decode()})
        spoken[-1]["images"] = pictures
        reply = await self.chat.think(Request(system_rest=system, messages=spoken,
                                              max_tokens=inputs.get("max_tokens")), process.agent_id)
        return {"text": reply.text, "stop": "length" if reply.stop == "length" else "end"}, []

    async def say(self, process: Process, call: Call, name: str, inputs: dict, message: dict) -> tuple[Any, list]:
        """Said in the chat; one that expects an answer is a question card, waited on. Nobody there: null at once."""
        await self.chat.gate.calls(process, call, "chat", 0)
        shown = {key: inputs[key] for key in ("text", "table", "chart", "choices") if key in inputs}
        expects = str(inputs.get("expects") or "nothing")
        if expects == "nothing":
            call.said += 1
            if call.said > self.CHATS_MOST:
                raise CallFailed("refused", f"A call says at most {self.CHATS_MOST} things in the chat.")
            await self.chat.say([{"type": "agent", "agent": process.agent_id, **shown}])
            return {"answer": None}, []
        if not call.attended:
            return {"answer": None}, []
        answer = await self.chat.asked("question", call, process.agent_id, {**shown, "expects": expects})
        found = (answer or {}).get(expects) if isinstance(answer, dict) else None
        if expects == "file" and found:
            offered = await process.offer(str(found))
            return {"answer": f"in/{offered['id']}/{offered['name']}"}, [offered]
        return {"answer": found}, []

    async def open(self, process: Process, call: Call, name: str, inputs: dict, message: dict) -> tuple[Any, list]:
        """The person sees the code, its purpose and the hosts; a yes opens them for this call."""
        hosts = [str(h).lower() for h in inputs.get("hosts") or []][:20]
        if not hosts:
            raise CallFailed("invalid", "Name the hosts to open.")
        function = process.function(call.function) or {}
        if "network.open" not in (function.get("calls") or []):
            raise CallFailed("refused", f"{process.agent_id}.{call.function} doesn't declare network.open.")
        if self.chat.gate.effect(f"agent:{process.agent_id}:{call.function}:network.open") == "deny":
            raise CallFailed("refused", "The organization's policy doesn't allow this.")
        if not call.attended:
            raise CallFailed("refused", "Nobody is there to approve this.")
        answer = await self.chat.asked("network", call, process.agent_id, {
            "hosts": hosts, "purpose": str(inputs.get("purpose") or "")[:1000], "code": inputs.get("code")})
        if not (answer or {}).get("allowed"):
            raise CallFailed("refused", "Its person declined.")
        call.opened.update(hosts)
        return {"hosts": hosts}, []

    @staticmethod
    def _revealed(process: Process, resource: str, fields: dict[str, Any]) -> None:
        """A secret's from_secret field, read: its host may be reached from now on."""
        for host in (process.manifest.get("network") or {}).get("hosts") or []:
            if isinstance(host, dict) and host["from_secret"].startswith(f"{resource}."):
                value = fields.get(host["from_secret"].split(".", 1)[1])
                if isinstance(value, str) and value:
                    process.secret_hosts.add(value.lower())
