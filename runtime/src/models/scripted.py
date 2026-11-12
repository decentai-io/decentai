"""A model that answers from a script, for tests: no network, no key (provider "fake")."""

from __future__ import annotations

from typing import Any

from .connector import Connector, OnText
from .usage import Usage


class Scripted(Connector):
    NAME = "Scripted"
    REQUIRED = ()
    STOPS = {"end": "end", "calls": "calls", "length": "length", "refusal": "refusal"}

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        #: Each line is the reply's words, or {"text", "calls": [{"name", "arguments"}], "stop"}.
        self.responses = list(config.get("responses") or [])
        #: Every request asked, in order.
        self.requests: list[Connector.Request] = []

    def _client(self) -> None:
        return None

    async def close(self) -> None:
        pass

    def build(self, request: Connector.Request) -> Connector.Request:
        return request

    async def send(self, body: Connector.Request, on_text: OnText) -> Connector.Reply:
        self.requests.append(body)
        if not self.responses:
            raise RuntimeError(f"Script exhausted after {len(self.requests)} request(s)")
        line = self.responses.pop(0)
        said = {"text": line} if isinstance(line, str) else line
        text = str(said.get("text") or "")
        await on_text(text)
        calls = [self.Call(str(call.get("id") or self.made_id()), call["name"],
                      self.arguments(call.get("arguments"))) for call in said.get("calls") or []]
        return self.reply(text, calls, [], said.get("stop") or "end", Usage())
