"""What one chat's programs call within their calls (record.*, model, chat, network.open), their notes and the files
they return, and what they may reach (agents.md §6, §8; runtime.md §6, §7)."""

from __future__ import annotations

import asyncio
import base64
import collections
import itertools
import posixpath
import secrets
from typing import Any

from shared.file_types import FileTypes
from ..models.connector import Connector
from .api_servers import ApiServers
from .process import Process


class Programs:
    """Given the chat it serves; its processes call back ``serve``, ``noted`` and ``keep``, and the proxy asks
    ``reach``."""

    #: The level of each function a program may call (agents.md §6); a level above every trust, so always asked.
    LEVELS = {"record.list": 0, "record.read": 0, "record.create": 1, "record.update": 1, "record.delete": 2,
              "model": 0, "chat": 0, "network.open": 3}
    ABOVE_TRUST = 4
    #: An API server's refusal, as the code a program's call ends with (agents.md §9); any other is "failed".
    CODES = {"invalid": "invalid", "too_large": "invalid", "refused": "refused", "conflict": "refused",
             "not_found": "not_found"}
    #: What one call may say in the chat; pictures a model is shown, by their bytes, and the largest.
    SAID_MOST, PICTURES, PICTURE_MOST = 5, ("image/png", "image/jpeg", "image/gif", "image/webp"), 5 * 1024 * 1024
    WEB_PORTS = (80, 443)

    def __init__(self, chat: Any):
        self.chat = chat
        #: Hosts written in secrets each agent read.
        self.revealed: dict[str, set[str]] = collections.defaultdict(set)
        self._ids = itertools.count(secrets.randbelow(2 ** 40))

    async def serve(self, call: Any, name: str, inputs: dict[str, Any],
                    files: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
        """A program's call to the runtime within ``call``, through the gate at its run's trust. Its output and the
        files offered back; Process.Failed."""
        level = self.LEVELS.get(name)
        if level is None:
            raise Process.Failed("not_found", f"The runtime has no function {name}.")
        what = f"{name}:{inputs.get('resource')}" if name.startswith("record.") else name
        item = {"id": f"{call.id}.{next(self._ids)}", "agent": call.agent, "function": call.function, "call": what,
                "record": str(inputs.get("ref") or ""), "level": level,
                "name": f"{self.chat.titled(call.agent, call.function)} → {what}", "inputs": self.chat.shown(inputs),
                "work": call.work, "within": True}
        # network.open asks the person with a card of its own: the gate judges it by the rules alone
        await self.chat.checked(0 if name == "network.open" else level, item, call.trust,
                                f"agent:{call.agent}:{call.function}:{what}", call)
        try:
            if name.startswith("record."):
                return await self._record(call, name.split(".")[1], inputs, files, item)
            if name == "model":
                return await self._model(call, inputs, files)
            if name == "chat":
                return await self._chat(call, inputs)
            return await self._open(call, inputs)
        except ApiServers.Refused as refused:
            raise Process.Failed(self.CODES.get(refused.code, "failed"), refused.message) from None

    async def noted(self, call: Any, note: dict[str, Any]) -> None:
        """A program's note, as the chat's event: a line of progress, or a picture of what it drives."""
        shown = {"agent": call.agent, "call": call.id, "work": call.work}
        if isinstance(note.get("progress"), str):
            self.chat.later(self.chat.event("progress", {**shown, "line": note["progress"][:1000]}))
        elif "screen" in note and len(str(note["screen"])) <= 400_000:
            await self.chat.event("screen", {**shown, "screen": note["screen"]})

    async def keep(self, name: str, data: bytes) -> str:
        """A file a program returned, kept as the chat's person's own, its bytes sent as they are: its ref."""
        made = await self.chat.api.call("Files:Piece:write", {"chat_id": self.chat.id, "name": name, "data": data})
        return made["record_id"]

    def reach(self, process: Process, protocol: str, host: str, port: int) -> Any:
        """What one of the chat's processes may reach, the same for TCP and UDP: the reason it may not; None when it
        may; or the future of the call whose network.open alone opened it, so it closes with that call."""
        blocked = (self.chat.contract.get("organization") or {}).get("blocked_hosts") or []
        if any(self.named(pattern, host) for pattern in blocked):
            return f"{host} is blocked by the organization's policy."
        agent = process.agent["agent_id"]
        declared = [h for h in (process.manifest.get("network") or {}).get("hosts") or [] if isinstance(h, str)]
        if "any" in declared or any(self.matches(p, host, port) for p in [*declared, *self.revealed.get(agent, ())]):
            return None
        call = next((c for c in process.calls.values()
                     if any(self.matches(p, host, port) for p in getattr(c, "opened", ()))), None)
        if call is None:
            return f"{host}:{port} is not a host this agent declared, or was opened for this call."
        return call.done

    # -- each function --------------------------------------------------------------------------------

    async def _record(self, call: Any, operation: str, inputs: dict[str, Any], files: list[dict[str, Any]],
                      item: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
        """Asked of an API server, naming the chat, the agent, the function and the call. One it says needs a person's
        approval is asked again once they give it; a secret it says a person must fill in waits for their form. What
        it touched is kept with the call."""
        chat = self.chat
        asked = {**inputs, "chat_id": chat.id, "agent": call.agent, "function": call.function, "call": call.id,
                 **({"schedule": call.origin[9:]} if call.origin.startswith("schedule:") else {})}
        if operation == "create" and files:
            data = await chat.engine.sandboxes.pull(files[0]["id"], chat.engine.FILE_MOST)
            asked["file_id"] = await self.keep(posixpath.basename(files[0]["path"]), data)
        try:
            answer = await chat.api.call(f"Records:Record:{operation}", asked)
        except ApiServers.Refused as refused:
            if not refused.error.get("approval"):
                raise
            await chat.checked(self.ABOVE_TRUST, item, call.trust, call=call)
            answer = await chat.api.call(f"Records:Record:{operation}", asked)
        if answer.get("waiting"):
            filled = await chat.cards.answer(answer.pop("waiting"), call, [(call.work, False)], within=True) or {}
            if filled.get("once"):
                answer["once"] = filled["once"]
        if operation != "list":
            call.records.append({"operation": operation, "resource": str(inputs.get("resource") or ""),
                                 "ref": str(answer.get("ref") or inputs.get("ref") or "")})
        if operation == "read" and "size" in answer and "path" not in answer:  # a file's: offered for it to read
            return ({**answer, "path": f"in/{answer['ref']}/{answer['name']}"},
                    [{"id": answer["ref"], "name": answer["name"], "size": answer["size"]}])
        if operation == "read":
            self._revealed(call.agent, str(inputs.get("resource") or ""), answer.get("fields") or {})
        return answer, []

    async def _model(self, call: Any, inputs: dict[str, Any],
                     files: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
        """The chat's model, with the pictures the program sent."""
        messages = [m for m in inputs.get("messages") or [] if isinstance(m, dict)]
        system = "\n\n".join(str(m.get("text") or "") for m in messages if m.get("role") == "system")
        spoken = [{"role": "assistant" if m.get("role") == "assistant" else "user", "text": str(m.get("text") or "")}
                  for m in messages if m.get("role") != "system"]
        if not spoken:
            raise Process.Failed("invalid", "messages holds at least one message that isn't the system's.")
        pictures = []
        for file in files[:16]:
            data = await self.chat.engine.sandboxes.pull(file["id"], self.PICTURE_MOST)
            kind = FileTypes.of(file["path"])
            if kind not in self.PICTURES or not FileTypes.agrees(kind, data[:16]):
                raise Process.Failed("invalid", f"{file['path']} is not a PNG, JPEG, GIF or WebP picture by its bytes.")
            pictures.append({"mime": kind, "data": (await asyncio.to_thread(base64.b64encode, data)).decode()})
        spoken[-1]["images"] = pictures
        reply = await self.chat.think(Connector.Request(system_rest=system, messages=spoken,
                                              max_tokens=inputs.get("max_tokens")), agent=call.agent)
        return {"text": reply.text, "stop": "length" if reply.stop == "length" else "end"}, []

    async def _chat(self, call: Any, inputs: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
        """Said in the chat; one that expects an answer is a question card, waited on whoever started the call."""
        shown = {key: inputs[key] for key in ("text", "table", "chart", "choices") if key in inputs}
        expects = str(inputs.get("expects") or "nothing")
        if expects == "nothing":
            call.said += 1
            if call.said > self.SAID_MOST:
                raise Process.Failed("refused", f"A call says at most {self.SAID_MOST} things in the chat.")
            await self.chat.say([{"type": "agent", "agent": call.agent, **shown}])
            return {"answer": None}, []
        cards = self.chat.cards
        card = await cards.open("question", {**shown, "expects": expects}, call.id, call.agent)
        answer = await cards.answer(card, call, [(call.work, False)], within=True)
        found = answer.get(expects) if isinstance(answer, dict) else None
        if expects == "file" and found:
            offered = await self.chat.offered(str(found))
            return {"answer": f"in/{offered['id']}/{offered['name']}"}, [offered]
        return {"answer": found}, []

    async def _open(self, call: Any, inputs: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
        """The person sees the code, its purpose and the hosts; a yes opens them until this call ends."""
        hosts = [str(host).lower() for host in inputs.get("hosts") or []][:20]
        if not hosts:
            raise Process.Failed("invalid", "Name the hosts to open.")
        request = {"hosts": hosts, "purpose": str(inputs.get("purpose") or "")[:1000], "code": inputs.get("code")}
        card = await self.chat.cards.open("network", request, call.id, call.agent)
        if not ((await self.chat.cards.answer(card, call, [(call.work, False)], within=True)) or {}).get("allowed"):
            raise Process.Failed("refused", "Its person declined.")
        call.opened.update(hosts)
        return {"hosts": hosts}, []

    def _revealed(self, agent_id: str, resource: str, fields: dict[str, Any]) -> None:
        """A secret's from_secret field read: its host may be reached by that agent from now on."""
        manifest = (self.chat.agents.get(agent_id) or {}).get("manifest") or {}
        for host in (manifest.get("network") or {}).get("hosts") or []:
            if isinstance(host, dict) and host["from_secret"].startswith(f"{resource}."):
                value = fields.get(host["from_secret"].split(".", 1)[1])
                if isinstance(value, str) and value:
                    self.revealed[agent_id].add(value.lower())

    @classmethod
    def matches(cls, pattern: str, host: str, port: int) -> bool:
        """A host pattern of a manifest: ``example.com``, ``*.example.com``, either with ``:port``; one without a port
        reaches the web's ports only."""
        name, _, wanted = pattern.partition(":")
        return (int(wanted) == port if wanted.isdigit() else not wanted and port in cls.WEB_PORTS) \
            and cls.named(name, host)

    @staticmethod
    def named(pattern: str, host: str) -> bool:
        pattern = pattern.lower()
        return host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:]))
