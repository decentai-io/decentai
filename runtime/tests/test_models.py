"""Each model connector reading a recorded stream: the bytes its provider sends, arriving in pieces of 7 bytes that
cut through every frame and line. Each says something, thinks, and calls a tool; the reply must hold all three. And the
guard every connector reaches its provider through."""

import json
import struct
import zlib

import httpx
import pytest

from runtime.models.anthropic import Anthropic
from runtime.models.bedrock import Bedrock
from runtime.models.connector import Connector
from runtime.models.gemini import Gemini
from runtime.models.guard import Guard
from runtime.models.openai import OpenAI
from runtime.models.responses import Responses
from runtime.models.usage import Usage

ASKED = Connector.Request(system_rest="Be brief.", messages=[{"role": "user", "text": "Hi"}])
LOOK = Connector.Call("t1", "look", {"q": "x"})
#: What every recording used: 10 tokens in, 3 read from the cache, 5 out; and 2 written to the cache or 2 of the 5
#: reasoning, where the protocol counts it.
USED = {"input": 10, "cache_read": 3, "output": 5}


class Recorded:
    """The provider: the refusals given first, then one recording answered to every request; the paths it was asked at
    and the bodies it was sent."""

    def __init__(self, body: bytes, kind: str, refusals: list[httpx.Response] = ()):
        self.body, self.kind, self.refusals, self.asked, self.sent = body, kind, list(refusals), [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request.url.raw_path.decode())
        self.sent.append(json.loads(request.content))
        if self.refusals:
            return self.refusals.pop(0)

        async def pieces():
            for at in range(0, len(self.body), 7):
                yield self.body[at:at + 7]
        return httpx.Response(200, headers={"content-type": self.kind}, content=pieces())


async def replay(cls: type[Connector], body: bytes, kind: str, monkeypatch, refusals=(), **config) -> tuple:
    """The connector's reply to the recording, the words it gave as they came, the paths it asked, and what it sent."""
    recorded = Recorded(body, kind, refusals)
    monkeypatch.setattr(Connector, "http", lambda self: httpx.AsyncClient(transport=httpx.MockTransport(recorded)))
    connector = cls({"api_key": "key", "model": "m", "endpoint": "https://provider.test", **config})
    told = []

    async def on_text(words):
        told.append(words)
    try:
        return await connector.stream(ASKED, on_text), "".join(told), recorded.asked, recorded.sent
    finally:
        await connector.close()


def sse(*events: dict, named: bool = False) -> bytes:
    """Server-sent events, each a line of JSON; named by its type where the protocol names them."""
    return b"".join((f"event: {event['type']}\n" if named else "").encode() + b"data: " + json.dumps(event).encode()
                    + b"\n\n" for event in events)


def event_stream(kind: str, payload: dict, message: str = "event") -> bytes:
    """One AWS event-stream message: its prelude and its checksum, its text headers, its JSON, its checksum."""
    names = {":event-type" if message == "event" else ":exception-type": kind, ":content-type": "application/json",
             ":message-type": message}
    headers = b"".join(bytes([len(name)]) + name.encode() + b"\x07" + struct.pack(">H", len(value)) + value.encode()
                       for name, value in names.items())
    body = json.dumps(payload).encode()
    prelude = struct.pack(">II", 16 + len(headers) + len(body), len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude))
    frame = prelude + headers + body
    return frame + struct.pack(">I", zlib.crc32(frame))


class TestBedrock:
    STREAM = b"".join(event_stream(kind, payload) for kind, payload in [
        ("messageStart", {"role": "assistant"}),
        ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"reasoningContent": {"text": "Thinking."}}}),
        ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"reasoningContent": {"signature": "sig"}}}),
        ("contentBlockStop", {"contentBlockIndex": 0}),
        ("contentBlockDelta", {"contentBlockIndex": 1, "delta": {"text": "Hello"}}),
        ("contentBlockDelta", {"contentBlockIndex": 1, "delta": {"text": " there"}}),
        ("contentBlockStop", {"contentBlockIndex": 1}),
        ("contentBlockStart", {"contentBlockIndex": 2, "start": {"toolUse": {"toolUseId": "t1", "name": "look"}}}),
        ("contentBlockDelta", {"contentBlockIndex": 2, "delta": {"toolUse": {"input": '{"q":'}}}),
        ("contentBlockDelta", {"contentBlockIndex": 2, "delta": {"toolUse": {"input": '"x"}'}}}),
        ("contentBlockStop", {"contentBlockIndex": 2}),
        ("messageStop", {"stopReason": "tool_use"}),
        ("metadata", {"usage": {"inputTokens": 10, "outputTokens": 5, "cacheReadInputTokens": 3,
                                "cacheWriteInputTokens": 2}}),
    ])

    async def test_its_event_stream_is_read_whole(self, monkeypatch):
        reply, told, asked, _ = await replay(Bedrock, self.STREAM, "application/vnd.amazon.eventstream", monkeypatch)
        assert (reply.text, told, reply.calls, reply.stop) == ("Hello there", "Hello there", [LOOK], "calls")
        assert reply.kept == [{"at": 0, "block": {"reasoningContent": {"reasoningText": {"text": "Thinking.",
                                                                                          "signature": "sig"}}}}]
        assert reply.usage == Usage(**USED, cache_write=2)
        assert asked == ["/model/m/converse-stream"]

    async def test_an_exception_in_the_stream_fails_the_call(self, monkeypatch):
        stream = event_stream("messageStart", {"role": "assistant"}) + \
            event_stream("throttlingException", {"message": "Too many requests."}, "exception")
        with pytest.raises(Connector.Failed, match="throttlingException: Too many requests"):
            await replay(Bedrock, stream, "application/vnd.amazon.eventstream", monkeypatch)


class TestAnthropic:
    STREAM = sse(
        {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "m", "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 3,
                                             "cache_creation_input_tokens": 2}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Thinking."}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hello"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": " there"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "tool_use", "id": "t1", "name": "look", "input": {}}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"q":'}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '"x"}'}},
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
         "usage": {"output_tokens": 5}},
        {"type": "message_stop"}, named=True)

    async def test_its_events_are_read_whole(self, monkeypatch):
        reply, told, asked, _ = await replay(Anthropic, self.STREAM, "text/event-stream", monkeypatch)
        assert (reply.text, told, reply.calls, reply.stop) == ("Hello there", "Hello there", [LOOK], "calls")
        assert reply.kept == [{"at": 0, "block": {"type": "thinking", "thinking": "Thinking.", "signature": "sig"}}]
        assert reply.usage == Usage(**USED, cache_write=2)
        assert asked == ["/v1/messages"]


class TestOpenAI:
    @staticmethod
    def chunk(**said) -> dict:
        return {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m", **said}

    def stream(self) -> bytes:
        def delta(finish=None, **delta):
            return self.chunk(choices=[{"index": 0, "delta": delta, "finish_reason": finish}])
        return sse(
            delta(role="assistant", content="Hello"), delta(content=" there"),
            delta(tool_calls=[{"index": 0, "id": "t1", "type": "function",
                               "function": {"name": "look", "arguments": '{"q":'}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": '"x"}'}}]),
            delta(finish="tool_calls"),
            self.chunk(choices=[], usage={"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18,
                                          "prompt_tokens_details": {"cached_tokens": 3},
                                          "completion_tokens_details": {"reasoning_tokens": 2}})) + b"data: [DONE]\n\n"

    async def test_its_chunks_are_read_whole(self, monkeypatch):
        reply, told, asked, _ = await replay(OpenAI, self.stream(), "text/event-stream", monkeypatch)
        assert (reply.text, told, reply.calls, reply.stop) == ("Hello there", "Hello there", [LOOK], "calls")
        assert reply.usage == Usage(**USED, reasoning=2)
        assert asked == ["/chat/completions"]

    async def test_an_effort_the_model_refuses_is_not_sent_again(self, monkeypatch):
        refused = httpx.Response(400, json={"error": {
            "message": "Unsupported parameter: 'reasoning_effort' is not supported with this model.",
            "type": "invalid_request_error", "param": "reasoning_effort", "code": "unsupported_parameter"}})
        reply, _, asked, sent = await replay(OpenAI, self.stream(), "text/event-stream", monkeypatch, [refused],
                                             reasoning_effort="high")
        assert reply.text == "Hello there" and len(asked) == 2
        assert sent[0]["reasoning_effort"] == "high" and "reasoning_effort" not in sent[1]

    async def test_a_server_of_ones_own_is_reached_with_no_key(self, monkeypatch):
        reply, _, asked, _ = await replay(OpenAI, self.stream(), "text/event-stream", monkeypatch, api_key="")
        assert reply.text == "Hello there" and asked == ["/chat/completions"]


class TestResponses:
    REASONING = {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "Thinking."}],
                 "encrypted_content": "sealed"}
    MESSAGE = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
               "content": [{"type": "output_text", "text": "Hello there", "annotations": []}]}
    CALL = {"type": "function_call", "id": "fc_1", "call_id": "t1", "name": "look", "arguments": '{"q":"x"}',
            "status": "completed"}
    STREAM = sse(
        {"type": "response.created", "sequence_number": 0,
         "response": {"id": "r1", "object": "response", "created_at": 1, "model": "m", "status": "in_progress",
                      "output": []}},
        {"type": "response.output_text.delta", "sequence_number": 1, "item_id": "msg_1", "output_index": 1,
         "content_index": 0, "delta": "Hello"},
        {"type": "response.output_text.delta", "sequence_number": 2, "item_id": "msg_1", "output_index": 1,
         "content_index": 0, "delta": " there"},
        {"type": "response.completed", "sequence_number": 3,
         "response": {"id": "r1", "object": "response", "created_at": 1, "model": "m", "status": "completed",
                      "output": [REASONING, MESSAGE, CALL], "incomplete_details": None,
                      "usage": {"input_tokens": 13, "input_tokens_details": {"cached_tokens": 3},
                                "output_tokens": 5, "output_tokens_details": {"reasoning_tokens": 2},
                                "total_tokens": 18}}}, named=True)

    async def test_its_events_are_read_whole(self, monkeypatch):
        reply, told, asked, _ = await replay(Responses, self.STREAM, "text/event-stream", monkeypatch)
        assert (reply.text, told, reply.calls, reply.stop) == ("Hello there", "Hello there", [LOOK], "calls")
        assert reply.kept == [self.REASONING, {key: value for key, value in self.MESSAGE.items() if key != "content"},
                              {key: value for key, value in self.CALL.items() if key != "arguments"}]
        assert reply.usage == Usage(**USED, reasoning=2)
        assert asked == ["/responses"]


class TestGemini:
    @staticmethod
    def said(*parts: dict, **more) -> dict:
        return {"candidates": [{"content": {"role": "model", "parts": list(parts)}, **more}]}

    STREAM = sse(
        said({"text": "Thinking.", "thought": True}), said({"text": "Hello"}),
        said({"text": " there", "thoughtSignature": "sig1"}),
        {**said({"functionCall": {"name": "look", "args": {"q": "x"}}, "thoughtSignature": "sig2"},
                finishReason="STOP"),
         "usageMetadata": {"promptTokenCount": 13, "cachedContentTokenCount": 3, "candidatesTokenCount": 3,
                           "thoughtsTokenCount": 2, "totalTokenCount": 18}})

    async def test_its_events_are_read_whole(self, monkeypatch):
        reply, told, asked, _ = await replay(Gemini, self.STREAM, "text/event-stream", monkeypatch)
        (call,) = reply.calls
        assert (reply.text, told, call.name, call.arguments, reply.stop) == ("Hello there", "Hello there", "look",
                                                                             {"q": "x"}, "calls")
        assert reply.kept == [{"call": "", "signature": "sig1"}, {"call": call.id, "signature": "sig2"}]
        assert reply.usage == Usage(**USED, reasoning=2)
        assert asked == ["/models/m:streamGenerateContent?alt=sse"]

    async def test_an_error_in_the_stream_fails_the_call(self, monkeypatch):
        with pytest.raises(Connector.Failed, match="The model is overloaded"):
            await replay(Gemini, sse(self.said({"text": "Hel"}), {"error": {"message": "The model is overloaded."}}),
                         "text/event-stream", monkeypatch)


class TestGuard:
    @staticmethod
    def guarded(monkeypatch, *answers: list[str], inside: list[str] = ()) -> tuple[Guard, list]:
        """A guard whose lookups answer these addresses in turn, and the requests it let through."""
        answered, seen = iter(answers), []

        async def lookup(host, port):
            return next(answered)

        monkeypatch.setattr("runtime.models.guard.lookup", lookup)
        guard = Guard(inside)
        guard.transport = httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200))
        return guard, seen

    async def test_it_reaches_only_the_address_it_checked_with_the_name_kept(self, monkeypatch):
        guard, seen = self.guarded(monkeypatch, ["8.8.8.8"], ["169.254.169.254"])
        async with httpx.AsyncClient(transport=guard) as client:
            await client.get("https://model.example.com/v1/models")
            with pytest.raises(httpx.ConnectError, match="model.example.com is not a public address"):
                await client.get("https://model.example.com/v1/models")
        (sent,) = seen
        assert (sent.url.host, sent.headers["host"], sent.extensions["sni_hostname"]) == (
            "8.8.8.8", "model.example.com", "model.example.com")

    async def test_the_operator_names_hosts_and_networks_inside_that_may_be_reached(self, monkeypatch):
        guard, seen = self.guarded(monkeypatch, ["10.1.2.3"], ["192.168.0.9"], inside=["10.0.0.0/8", "llm.lan"])
        async with httpx.AsyncClient(transport=guard) as client:
            await client.get("http://models.internal:8000/v1")
            await client.get("http://llm.lan/v1")
        assert [request.url.host for request in seen] == ["10.1.2.3", "192.168.0.9"]
