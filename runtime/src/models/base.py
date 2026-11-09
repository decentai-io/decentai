"""What every connector shares: the neutral request and reply, the one request loop, plain HTTP, and tools
in words for a connection without native ones."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Awaitable, Callable
from urllib.parse import quote

import httpx

from .cache import Cache, Turn
from .usage import Usage

OnText = Callable[[str], Awaitable[None]]


class NoModel(RuntimeError):
    """A chat with no model to think with. The message is the platform's own sentence for why."""


class ProviderError(RuntimeError):
    """A provider refused or failed, in its own words, with the status it answered."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Call:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Reply:
    text: str
    calls: list[Call]
    kept: list
    model: str
    stop: str       # "end", "calls", "length" or "refusal"
    usage: Usage


@dataclass
class Request:
    system_stable: str = ""
    system_rest: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    through: int = -1
    max_tokens: int | None = None


class Connector:
    NAME = "The provider"
    REQUIRED = ("api_key", "model")
    TIMEOUT_SECONDS = 300
    CONNECT_SECONDS = 10
    #: Asking again, a busy or unreachable provider: waits of 0.5, 1, 2 and 4 seconds ride out a blip.
    MAX_RETRIES = 4
    #: How many times one request is built, when a refusal teaches the connector something (``learned``).
    ATTEMPTS = 3
    #: Begins every id made here, for a provider that gave none.
    MADE_UP = "made_"
    #: The provider's words for why it stopped; anything else is "end".
    STOPS: dict[str, str] = {}
    #: A tool with no description is described by its name.
    BY_NAME = False
    #: How ``spoken`` spells a turn.
    ASSISTANT = "assistant"
    CONTENT = "content"
    #: A script answers in order, so one that names chats must be told to expect it.
    names_chats = True

    #: Providers word these refusals their own way, so each test is broad and its consequence small.
    IMAGE_REFUSAL_MARKS = ("image_url", "image url", "image content", "invalid content type",
                           "unsupported content", "content type not supported", "multimodal",
                           "does not support image", "image input")
    VISION = re.compile(r"\bvision\b")
    CONTEXT_OVERFLOW_MARKS = ("context_length", "context length", "context window", "maximum context",
                              "too many tokens", "prompt is too long", "input is too long",
                              "request too large", "exceeds the limit", "token limit",
                              "exceeds the maximum number of tokens", "maximum model length")
    ACCOUNT_MARKS = ("quota", "credit", "billing", "payment", "insufficient_funds", "insufficient funds",
                     "balance")

    def __init__(self, config: dict[str, Any]):
        self.api_key = str(config.get("api_key") or "")
        self.model = str(config.get("model") or "")
        self.endpoint = str(config.get("endpoint") or "").strip().rstrip("/")
        for name in self.REQUIRED:
            if not getattr(self, name):
                raise ValueError(f"{self.NAME} connector requires {name}")
        self.limit = httpx.Timeout(self.setting(config, "timeout_seconds", self.TIMEOUT_SECONDS, 1, 600),
                                   connect=self.CONNECT_SECONDS)
        self.max_retries = int(self.setting(config, "max_retries", self.MAX_RETRIES, 0, 10))
        #: Sent only when the connection set it: a model that does not reason would refuse it.
        self.reasoning_effort = str(config.get("reasoning_effort") or "").strip().lower()
        #: A hash of the chat, for a provider that groups prompts by a key (``cache_for``).
        self.cache_key = ""
        self.client = self._client()

    @staticmethod
    def setting(config: dict[str, Any], name: str, default: float, low: float, high: float) -> float:
        """A number a connection sets, held to what makes sense. ``max_retries: 0`` gets no retries."""
        said = config.get(name)
        if said is None or isinstance(said, bool):
            return default
        try:
            value = float(said)
        except (TypeError, ValueError):
            return default
        if value != value:
            return default
        if value < low:
            return low if low == 0 else default
        return min(value, high)

    def _client(self) -> Any:
        """The provider's client, made once for this connector."""
        raise NotImplementedError

    def http(self) -> httpx.AsyncClient:
        """A redirect is not followed: it would carry the key in this client's headers to another origin."""
        return httpx.AsyncClient(timeout=self.limit, follow_redirects=False)

    async def close(self) -> None:
        await self.client.close()

    def cache_for(self, session: str) -> None:
        """Group this connector's requests by one chat: a short hash of its id, never the id."""
        self.cache_key = hashlib.sha256(str(session).encode("utf-8")).hexdigest()[:16] if session else ""

    # -- asking --------------------------------------------------------------------------------

    async def stream(self, request: Request, on_text: OnText | None = None) -> Reply:
        """One reply, its words given to ``on_text`` as they arrive. A refusal that taught something is
        asked again, built anew, and only before any word was given."""
        said = False

        async def told(delta: str) -> None:
            nonlocal said
            if delta:
                said = True
                if on_text is not None:
                    await on_text(delta)

        attempt = 1
        while True:
            try:
                return await self.send(self.build(request), told)
            except Exception as exc:
                if said or attempt >= self.ATTEMPTS or not self.learned(exc):
                    raise
            attempt += 1

    async def complete(self, system: str, text: str, max_tokens: int | None = None,
                       timeout: float | None = None) -> Reply:
        """One prompt, no tools."""
        request = Request(system_rest=system, messages=[{"role": "user", "text": text}], max_tokens=max_tokens)
        return await asyncio.wait_for(self.stream(request), timeout)

    def build(self, request: Request) -> Any:
        """The provider's request, from what this connector knows now."""
        raise NotImplementedError

    async def send(self, body: Any, on_text: OnText) -> Reply:
        raise NotImplementedError

    def learned(self, exc: Exception) -> bool:
        """Whether this refusal changed what ``build`` will say, so that asking again can differ."""
        return False

    # -- the neutral shapes, this protocol's way -------------------------------------------------

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
            role = item.get("role")
            if role == "tool":
                out += self.result(item)
            elif role == "assistant":
                out += self.assistant(item)
            else:
                if item.get("text"):
                    out.append(self.words(item["text"]))
                out += [self.image(image) for image in item.get("images") or []]
        return out

    def spoken(self, turns: list[Turn], budget: int, *before: list) -> list[dict[str, Any]]:
        """The turns as this protocol's messages, cache marks on ``before`` and at the kept stretch's end."""
        heads = [self.blocks(turn.items[:turn.through]) for turn in turns]
        Cache.place(self.mark, budget, *before, *heads)
        return [{"role": self.ASSISTANT if turn.role == "assistant" else "user",
                 self.CONTENT: head + self.blocks(turn.items[turn.through:])}
                for turn, head in zip(turns, heads)]

    def system(self, request: Request, said: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The system prompt's stable part and its rest, each as blocks."""
        rest = Cache.joined(request.system_rest, said)
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

    def reply(self, text: str, calls: list[Call], kept: list, said: str, usage: Usage) -> Reply:
        """The neutral answer. A refusal says nothing and calls nothing; a call cut at the cap is not run."""
        stop = self.STOPS.get(said, "end")
        if stop == "refusal":
            return Reply("", [], [], self.model, stop, usage)
        if stop != "length":
            stop = "calls" if calls else "end"
        elif calls:
            calls, kept = [], []
        return Reply(text, calls, kept, self.model, stop, usage)

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

    # -- reading a refusal -----------------------------------------------------------------------

    @classmethod
    def _about_the_account(cls, exc: Exception, reason: str) -> bool:
        return getattr(exc, "status_code", None) == 402 or any(mark in reason for mark in cls.ACCOUNT_MARKS)

    @classmethod
    def is_context_overflow(cls, exc: Exception) -> bool:
        """Whether the transcript outgrew the model's window: the one refusal a fold can answer."""
        reason = str(exc).lower()
        return not cls._about_the_account(exc, reason) and any(
            mark in reason for mark in cls.CONTEXT_OVERFLOW_MARKS)

    @classmethod
    def is_image_refusal(cls, exc: Exception) -> bool:
        """Whether a provider rejected a picture. A rate limit or an empty account may name a vision model."""
        reason = str(exc).lower()
        if getattr(exc, "status_code", None) == 429 or "rate limit" in reason or "rate_limit" in reason \
                or cls._about_the_account(exc, reason) or cls.is_context_overflow(exc):
            return False  # a window outgrown may count its picture tokens, and is answered by a fold
        return any(mark in reason for mark in cls.IMAGE_REFUSAL_MARKS) or cls.VISION.search(reason) is not None


class HttpConnector(Connector):
    """A provider reached with no client library of its own."""
    REQUIRED = ("api_key", "model", "endpoint")
    #: The answers worth asking again for: the server was busy or broken, not the request wrong.
    RETRIED = (408, 409, 429, 500, 502, 503, 504, 529)
    #: Seconds before the first retry; doubled for each one after.
    RETRY_WAIT = 0.5
    #: Where a request goes, from ``endpoint``, ``model`` and ``verb``.
    URL = ""

    def _client(self) -> httpx.AsyncClient:
        return self.http()

    async def close(self) -> None:
        await self.client.aclose()

    async def _auth(self) -> dict[str, str]:
        """The headers that sign one POST."""
        raise NotImplementedError

    def _url(self, verb: str) -> str:
        # The id may be an ARN, whose slashes and colons are part of one path segment.
        return self.URL.format(endpoint=self.endpoint, model=quote(self.model, safe=""), verb=verb)

    @staticmethod
    def _reason(response: httpx.Response) -> str:
        """Why it refused, in the provider's words: ``message`` at the top or under ``error``, else the body."""
        try:
            answer = response.json()
        except ValueError:
            return response.text
        if isinstance(answer, dict):
            error = answer.get("error")
            nested = error.get("message") if isinstance(error, dict) else None
            return str(answer.get("message") or nested or response.text)
        return response.text

    async def _open(self, verb: str, body: dict[str, Any]) -> httpx.Response:
        """The answer, open for reading. A busy server is asked again here and at no later moment."""
        headers = await self._auth()
        attempt = 0
        while True:
            try:
                response = await self.client.send(
                    self.client.build_request("POST", self._url(verb), json=body, headers=headers), stream=True)
            except httpx.TransportError:
                if attempt >= self.max_retries:
                    raise
            else:
                if response.status_code < 300:
                    return response
                await response.aread()
                await response.aclose()
                if response.status_code not in self.RETRIED or attempt >= self.max_retries:
                    raise ProviderError(f"{self.NAME} answered {response.status_code}: "
                                        f"{self._reason(response)[:600]}", response.status_code)
            await asyncio.sleep(self.RETRY_WAIT * (2 ** attempt))
            attempt += 1

    @contextlib.asynccontextmanager
    async def opened(self, verb: str, body: dict[str, Any]) -> AsyncIterator[httpx.Response]:
        response = await self._open(verb, body)
        try:
            yield response
        finally:
            await response.aclose()

    async def _post(self, verb: str, body: dict[str, Any]) -> Any:
        async with self.opened(verb, body) as response:
            await response.aread()
            return response.json()


class TextTools(Connector):
    """A connection whose model has no native tools (``"tool_mode": "text"``): they are described in the
    prompt, and JSON in the reply is read back as calls."""
    PROMPT = ("You have these tools:\n{tools}\n\nTo use them, answer with only this JSON and no other words: "
              '{{"calls": [{{"name": "<tool>", "arguments": {{}}}}]}}. Otherwise answer in words.')
    MALFORMED = 'That was not valid JSON. Answer with only {"calls": [{"name": "<tool>", "arguments": {}}]}.'

    def __init__(self, inner: Connector):
        self.inner = inner
        self.model = inner.model
        self.names_chats = inner.names_chats

    async def close(self) -> None:
        await self.inner.close()

    def cache_for(self, session: str) -> None:
        self.inner.cache_for(session)

    async def stream(self, request: Request, on_text: OnText | None = None) -> Reply:
        messages, through = self._plain(request)
        tools = "\n".join(json.dumps(tool) for tool in request.tools)
        system = self.PROMPT.format(tools=tools) if request.tools else ""
        plain = replace(request, system_rest=Cache.joined(system, request.system_rest), messages=messages,
                        tools=[], through=through)
        held, free = "", False

        async def told(delta: str) -> None:
            """Words pass as they come, unless they may turn out to be the JSON."""
            nonlocal held, free
            if free:
                await on_text(delta)
                return
            held += delta
            if held.lstrip()[:1] not in ("", "{", "`"):
                free = True
                await on_text(held)

        spent = Usage()  # both attempts are paid for, so both are metered
        for attempt in (1, 2):
            held, free = "", False
            reply = await self.inner.stream(plain, told if on_text is not None else None)
            spent += reply.usage
            reply = replace(reply, usage=spent)
            calls = self._calls(reply.text)
            if calls:
                return replace(reply, text="", calls=calls, stop="calls")
            if attempt == 1 and not free and self._malformed(reply.text):
                plain = replace(plain, messages=[*plain.messages, {"role": "assistant", "text": reply.text},
                                                 {"role": "user", "text": self.MALFORMED}])
                continue
            break
        if on_text is not None and reply.text and not free:
            await on_text(reply.text)
        return reply

    @staticmethod
    def _malformed(text: str) -> bool:
        """Meant as a call, and not readable as one: a model that wrote prose is not asked again."""
        return text.lstrip()[:1] in ("{", "`") and '"calls"' in text

    @classmethod
    def _calls(cls, text: str) -> list[Call]:
        body = text.strip()
        if body.startswith("```"):
            body = body.strip("`").removeprefix("json").strip()
        try:
            said = json.loads(body)
        except ValueError:
            return []
        wanted = said.get("calls") if isinstance(said, dict) else None
        if not isinstance(wanted, list):
            return []
        return [Call(cls.made_id(), str(call["name"]), cls.arguments(call.get("arguments")))
                for call in wanted if isinstance(call, dict) and call.get("name")]

    @staticmethod
    def _plain(request: Request) -> tuple[list[dict[str, Any]], int]:
        """The conversation in words alone, one reply's results in one message; where the kept part ends."""
        out: list[dict[str, Any]] = []
        through = -1
        for index, message in enumerate(request.messages):
            role = message.get("role")
            if role == "tool":
                failed = " (failed)" if message.get("is_error") else ""
                text = f"Result of {message.get('name')}{failed}:\n{message.get('text') or ''}"
                images = list(message.get("images") or [])
                if index and request.messages[index - 1].get("role") == "tool":
                    out[-1] = {**out[-1], "text": Cache.joined(out[-1]["text"], text),
                               "images": [*out[-1]["images"], *images]}
                else:
                    out.append({"role": "user", "text": text, "images": images})
            elif role == "assistant" and message.get("calls"):
                said = json.dumps({"calls": [{"name": call["name"], "arguments": call["arguments"]}
                                             for call in message["calls"]]})
                out.append({**message, "text": Cache.joined(message.get("text"), said), "calls": []})
            else:
                out.append(message)
            if index <= request.through:
                through = len(out) - 1
        return out, through
