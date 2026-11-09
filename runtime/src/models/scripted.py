"""A model that answers from a script: no network, no key. Registered as provider "fake", so a chat config can
script a whole conversation. It still only proposes: every call goes through the gates."""

from __future__ import annotations

from typing import Any

from .base import Call, Connector, OnText, Reply, Request
from .usage import Usage


class Scripted(Connector):
    NAME = "Scripted"
    REQUIRED = ()
    STOPS = {"end": "end", "calls": "calls", "length": "length", "refusal": "refusal"}
    #: A script answers in order: naming a chat would eat the next line.
    names_chats = False

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        #: Each line is the reply's words, or {"text", "calls": [{"name", "arguments"}], "stop"}.
        self.responses = list(config.get("responses") or [])
        #: Every request asked, in order.
        self.requests: list[Request] = []

    def _client(self) -> None:
        return None

    async def close(self) -> None:
        pass

    def build(self, request: Request) -> Request:
        return request

    async def send(self, body: Request, on_text: OnText) -> Reply:
        self.requests.append(body)
        if not self.responses:
            raise RuntimeError(f"Script exhausted after {len(self.requests)} request(s)")
        line = self.responses.pop(0)
        said = {"text": line} if isinstance(line, str) else line
        text = str(said.get("text") or "")
        await on_text(text)
        calls = [Call(str(call.get("id") or self.made_id()), call["name"],
                      self.arguments(call.get("arguments"))) for call in said.get("calls") or []]
        return self.reply(text, calls, [], said.get("stop") or "end", Usage())
