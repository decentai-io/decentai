"""What every connector shares: the neutral request and reply, the one request loop, cache marks, and reading a
refusal."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from .guard import Guard
from .usage import Usage

OnText = Callable[[str], Awaitable[None]]


class Connector:
    """One connection's model, spoken in one protocol: ``stream(request, on_text)`` gives a Reply or raises Failed, and
    ``close()`` ends it. Each protocol says how to ``build`` and ``send`` a request, and what a refusal taught it
    (``learned``)."""

    @dataclass
    class Request:
        """Instructions in a part that stays and the rest; the conversation, each message ``{role, text, calls,
        images, kept, model}`` or a tool's answer ``{role: tool, call_id, name, text, is_error, images}``; the tools,
        each ``{name, description, parameters}``; whether the conversation is cached; a cap on the reply."""
        system_stable: str = ""
        system_rest: str = ""
        messages: list[dict[str, Any]] = field(default_factory=list)
        tools: list[dict[str, Any]] = field(default_factory=list)
        cached: bool = False
        max_tokens: int | None = None

    @dataclass
    class Call:
        id: str
        name: str
        arguments: dict[str, Any]

    @dataclass
    class Reply:
        """``stop`` is end, calls, length or refusal; ``kept`` is reasoning, sent back only to the model that wrote
        it."""
        text: str
        calls: list[Connector.Call]
        kept: list
        model: str
        stop: str
        usage: Usage

    class Failed(Exception):
        """A provider refused or failed, in its own words, with the status it answered."""

        def __init__(self, message: str, status: int | None = None):
            super().__init__(message)
            self.message, self.status = message, status

    NAME = "The provider"
    REQUIRED = ("api_key", "model")
    #: Seconds a provider may stay silent, and to connect; tries again for one that is busy or unreachable.
    TIMEOUT, CONNECT, RETRIES = 300, 10, 2
    #: How many times one request is built, while each refusal teaches the connector something (``learned``).
    ATTEMPTS = 4
    #: Cache marks one request may hold (Anthropic's and Bedrock's alike).
    MARKS = 4
    #: Begins every id made here, for a provider that gave none.
    MADE_UP = "made_"
    #: The provider's words for why it stopped; anything else is "end".
    STOPS: dict[str, str] = {}
    #: A tool with no description is described by its name.
    BY_NAME = False
    #: How ``spoken`` spells a turn.
    ASSISTANT, CONTENT = "assistant", "content"
    #: Providers word these refusals their own way, so each test is broad and its consequence small.
    IMAGE_REFUSAL_MARKS = ("image_url", "image url", "image content", "invalid content type", "unsupported content",
                           "content type not supported", "multimodal", "does not support image", "image input")
    VISION = re.compile(r"\bvision\b")
    CONTEXT_OVERFLOW_MARKS = ("context_length", "context length", "context window", "maximum context",
                              "too many tokens", "prompt is too long", "input is too long", "request too large",
                              "exceeds the limit", "token limit", "exceeds the maximum number of tokens",
                              "maximum model length")
    ACCOUNT_MARKS = ("quota", "credit", "billing", "payment", "insufficient_funds", "insufficient funds", "balance")

    def __init__(self, config: dict[str, Any]):
        """``config``: ``api_key``, ``model``, ``endpoint`` and ``reasoning_effort``; ``vertex`` when a Google service
        account signs it in; ``inside``, the hosts and networks it may reach that aren't public; ``session``, what its
        requests are grouped by."""
        self.vertex = bool(config.get("vertex"))
        if self.vertex:
            self.NAME = "Vertex AI"
        self.api_key, self.model = str(config.get("api_key") or ""), str(config.get("model") or "")
        self.endpoint = str(config.get("endpoint") or "").strip().rstrip("/")
        for name in self.REQUIRED:
            if not getattr(self, name):
                raise ValueError(f"{self.NAME} connector requires {name}")
        #: Sent only when the chat set it, and no more once the model refused it.
        self.effort = str(config.get("reasoning_effort") or "").strip().lower()
        self.inside = list(config.get("inside") or [])
        #: A short hash of the session, never the session, for a provider that groups prompts by a key.
        session = str(config.get("session") or "")
        self.cache_key = hashlib.sha256(session.encode()).hexdigest()[:16] if session else ""
        self.limit = httpx.Timeout(self.TIMEOUT, connect=self.CONNECT)
        self.client = self._client()

    def _client(self) -> Any:
        """The provider's client, made once for this connector."""
        raise NotImplementedError

    def http(self) -> httpx.AsyncClient:
        """No redirect followed (it would carry the key elsewhere), no proxy from the environment, and only checked
        addresses reached."""
        return httpx.AsyncClient(timeout=self.limit, follow_redirects=False, trust_env=False,
                                 transport=Guard(self.inside))

    async def close(self) -> None:
        await self.client.close()

    # -- asking ---------------------------------------------------------------------------------------

    async def stream(self, request: Connector.Request, on_text: OnText | None = None) -> Connector.Reply:
        """One reply, its words given to ``on_text`` as they come. A refusal that taught something is asked again,
        built anew, and only before any word was given."""
        said = False

        async def told(delta: str) -> None:
            nonlocal said
            if delta:
                said = True
                if on_text is not None:
                    await on_text(delta)

        for attempt in range(1, self.ATTEMPTS + 1):
            try:
                return await self.send(self.build(request), told)
            except Exception as error:
                if said or attempt == self.ATTEMPTS or not self.learned(error):
                    raise self.failed(error) from error

    async def complete(self, system: str, text: str, max_tokens: int | None = None) -> Connector.Reply:
        """One prompt, no tools."""
        return await self.stream(self.Request(system_rest=system, messages=[{"role": "user", "text": text}],
                                              max_tokens=max_tokens))

    def build(self, request: Connector.Request) -> Any:
        """The provider's request, from what this connector knows now."""
        raise NotImplementedError

    async def send(self, body: Any, on_text: OnText) -> Connector.Reply:
        raise NotImplementedError

    def learned(self, error: Exception) -> bool:
        """Whether this refusal changed what ``build`` will say, so that asking again can differ."""
        return False

    def refused_effort(self, error: Exception, *marks: str) -> bool:
        """An effort the model doesn't take, named in its refusal: not sent again."""
        if self.effort and any(mark in str(error).lower() for mark in marks):
            self.effort = ""
            return True
        return False

    # -- the neutral shapes, this protocol's way ------------------------------------------------------

    def words(self, text: str) -> dict[str, Any]:
        return {"text": text}

    def image(self, image: dict[str, str]) -> dict[str, Any]:
        raise NotImplementedError

    def assistant(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def result(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def mark(self, blocks: list) -> None:
        """End ``blocks`` with this protocol's cache mark."""
        raise NotImplementedError

    def spec(self, tool: dict[str, Any]) -> dict[str, Any]:
        """A tool with what a protocol fills in where it says nothing; each protocol spells the result."""
        name = tool["name"]
        return {"name": name, "description": tool.get("description") or (name if self.BY_NAME else ""),
                "parameters": tool.get("parameters") or {"type": "object", "properties": {}}}

    def blocks(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in items:
            if item.get("role") == "tool":
                out += self.result(item)
            elif item.get("role") == "assistant":
                out += self.assistant(item)
            else:
                out += [self.words(item["text"])] if item.get("text") else []
                out += [self.image(image) for image in item.get("images") or []]
        return out

    @classmethod
    def turns(cls, request: Connector.Request, strict: bool = False) -> tuple[str, list[tuple[str, list]]]:
        """The words of any system message, and the conversation as ``(side, messages)`` turns: no empty message, one
        reply's results together, and with ``strict`` never two turns of one side in a row."""
        system: list[str] = []
        turns: list[tuple[str, list]] = []
        for message in request.messages:
            role = message.get("role")
            if role == "system":
                system.append(str(message.get("text") or ""))
                continue
            if role != "tool" and not (message.get("text") or message.get("calls") or message.get("images")):
                continue
            side = "assistant" if role == "assistant" else "user"
            last = turns[-1] if turns else None
            results = role == "tool" and last is not None and last[1][-1].get("role") == "tool"
            if last is not None and last[0] == side and (strict or results):
                last[1].append(message)
            else:
                turns.append((side, [message]))
        return cls.joined(*system), turns

    def spoken(self, request: Connector.Request, turns: list[tuple[str, list]], marks: int,
               *before: list) -> list[dict[str, Any]]:
        """The turns as this protocol's messages. Cache marks, ``marks`` at most, end each of ``before`` that holds
        anything, then the conversation when it is cached."""
        messages = [{"role": self.ASSISTANT if side == "assistant" else "user", self.CONTENT: self.blocks(items)}
                    for side, items in turns]
        last = [messages[-1][self.CONTENT]] if request.cached and messages else []
        for blocks in [place for place in (*before, *last) if place][:marks]:
            self.mark(blocks)
        return messages

    def system(self, request: Connector.Request, said: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The instructions' part that stays and their rest, each as blocks."""
        rest = self.joined(request.system_rest, said)
        return ([self.words(request.system_stable)] if request.system_stable else [],
                [self.words(rest)] if rest else [])

    def kept(self, message: dict[str, Any]) -> list:
        """A message's reasoning, only to the model that wrote it."""
        return (message.get("kept") or []) if message.get("model") == self.model else []

    def with_kept(self, message: dict[str, Any], content: list) -> list:
        """Blocks with the reasoning back where it was: each kept piece says ``at`` which place."""
        for piece in reversed(self.kept(message)):
            content.insert(piece["at"], piece["block"])
        return content

    def reply(self, text: str, calls: list[Connector.Call], kept: list, said: str, usage: Usage) -> Connector.Reply:
        """The neutral answer. A refusal says nothing and calls nothing; a call cut at the cap is not run."""
        stop = self.STOPS.get(said, "end")
        if stop == "refusal":
            return self.Reply("", [], [], self.model, stop, usage)
        if stop != "length":
            stop = "calls" if calls else "end"
        elif calls:
            calls, kept = [], []
        return self.Reply(text, calls, kept, self.model, stop, usage)

    @staticmethod
    def arguments(raw: Any) -> dict[str, Any]:
        """A call's arguments as a dict: JSON text or parsed already, and a model may send garbage."""
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def made_id(cls) -> str:
        return f"{cls.MADE_UP}{uuid.uuid4().hex[:24]}"

    @staticmethod
    def joined(*parts: Any) -> str:
        return "\n\n".join(str(part) for part in parts if part)

    # -- reading a refusal ----------------------------------------------------------------------------

    @classmethod
    def failed(cls, error: Exception) -> Connector.Failed:
        """Any failure as Failed, with the words of what caused it when a library hides them behind its own."""
        if isinstance(error, cls.Failed):
            return error
        words, cause = str(error) or type(error).__name__, error.__cause__
        if cause is not None and str(cause) and str(cause) not in words:
            words = f"{words} {cause}"
        return cls.Failed(words, getattr(error, "status_code", None))

    @classmethod
    def _about_the_account(cls, error: Exception, reason: str) -> bool:
        return getattr(error, "status", None) == 402 or any(mark in reason for mark in cls.ACCOUNT_MARKS)

    @classmethod
    def is_context_overflow(cls, error: Exception) -> bool:
        """Whether the conversation outgrew the model's window: the one refusal a fold can answer."""
        reason = str(error).lower()
        return not cls._about_the_account(error, reason) and any(m in reason for m in cls.CONTEXT_OVERFLOW_MARKS)

    @classmethod
    def is_image_refusal(cls, error: Exception) -> bool:
        """Whether a provider rejected a picture. A rate limit or an empty account may name a vision model, and a
        window outgrown may count its pictures: neither is one."""
        reason = str(error).lower()
        if getattr(error, "status", None) == 429 or "rate limit" in reason or "rate_limit" in reason \
                or cls._about_the_account(error, reason) or cls.is_context_overflow(error):
            return False
        return any(mark in reason for mark in cls.IMAGE_REFUSAL_MARKS) or cls.VISION.search(reason) is not None
