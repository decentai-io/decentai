"""What each model call used, read the same way from every provider
(llms/connector/usage.py), and every call a chat makes written down
and sent to the platform without the chat waiting on it (llms/meter.py)
— docs/system/monitoring.md, token usage."""

import asyncio
import json
import logging
import time
from types import SimpleNamespace

import httpx
import pytest

from ai_runtime.llms.connector.anthropic import AnthropicConnector
from ai_runtime.llms.connector.openai import OpenAIConnector
from ai_runtime.llms.connector.tools import ModelReply
from ai_runtime.llms.connector.usage import Embeddings, Usage
from ai_runtime.llms.factory import LLMConnectorFactory
from ai_runtime.llms.meter import ON_BEHALF, Meter
from ai_runtime.tests import fake_container
from ai_runtime.tests.test_llm_connectors import (
    TestBedrock, TestGemini, TestOpenAIResponses, service_account_key)


def run(awaitable):
    return fake_container.run(awaitable)


# ======================================================================
# One meaning for every provider
# ======================================================================

class TestUsageMeansOneThing:
    def test_openais_prompt_includes_what_it_read_from_the_cache(self):
        usage = Usage.openai(SimpleNamespace(
            prompt_tokens=1200, completion_tokens=300,
            prompt_tokens_details=SimpleNamespace(cached_tokens=1000),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=120)))
        assert usage.to_dict() == {"input": 200, "output": 300, "cache_read": 1000,
                                   "cache_write": 0, "reasoning": 120, "reported": True}
        assert usage.prompt == 1200

    def test_a_compatible_server_that_says_less_says_what_it_says(self):
        usage = Usage.openai({"prompt_tokens": 50, "completion_tokens": 5})
        assert (usage.input, usage.output, usage.cache_read) == (50, 5, 0)
        assert Usage.openai(None) == Usage()
        assert not Usage.openai(None).reported

    def test_responses_counts_the_same_way_under_other_names(self):
        usage = Usage.responses({
            "input_tokens": 900, "output_tokens": 40,
            "input_tokens_details": {"cached_tokens": 800},
            "output_tokens_details": {"reasoning_tokens": 30}})
        assert (usage.input, usage.cache_read, usage.output, usage.reasoning) == (100, 800, 40, 30)

    def test_anthropics_input_already_leaves_the_cache_out(self):
        usage = Usage.anthropic(SimpleNamespace(
            input_tokens=30, output_tokens=12,
            cache_read_input_tokens=5000, cache_creation_input_tokens=700))
        assert (usage.input, usage.cache_read, usage.cache_write, usage.output) == (30, 5000, 700, 12)
        assert usage.prompt == 5730

    def test_an_older_anthropic_answer_without_cache_fields(self):
        usage = Usage.anthropic(SimpleNamespace(input_tokens=30, output_tokens=12))
        assert (usage.input, usage.cache_read, usage.cache_write) == (30, 0, 0)

    def test_gemini_includes_the_cache_and_counts_thinking_apart(self):
        usage = Usage.gemini({
            "promptTokenCount": 1000, "cachedContentTokenCount": 600,
            "candidatesTokenCount": 50, "thoughtsTokenCount": 200,
            "toolUsePromptTokenCount": 10})
        assert (usage.input, usage.cache_read, usage.output, usage.reasoning) == (410, 600, 250, 200)

    def test_bedrock_leaves_the_cache_out_like_anthropic(self):
        usage = Usage.bedrock({"inputTokens": 20, "outputTokens": 9,
                               "cacheReadInputTokens": 400, "cacheWriteInputTokens": 90})
        assert (usage.input, usage.cache_read, usage.cache_write, usage.output) == (20, 400, 90, 9)

    def test_what_is_not_a_count_is_none(self):
        usage = Usage.bedrock({"inputTokens": -4, "outputTokens": "9",
                               "cacheReadInputTokens": True, "cacheWriteInputTokens": None})
        assert usage.to_dict() == {"input": 0, "output": 0, "cache_read": 0,
                                   "cache_write": 0, "reasoning": 0, "reported": True}
        assert Usage.gemini("nonsense") == Usage()
        # A server that says more was cached than it was shown.
        assert Usage.openai({"prompt_tokens": 10, "prompt_tokens_details": {
            "cached_tokens": 50}}).input == 0

    def test_a_reply_with_no_usage_carries_zeros(self):
        assert ModelReply(prose="hi").usage == Usage()


# ======================================================================
# Each connector reads its provider's answer
# ======================================================================

class TestEachConnectorReadsIt:
    def test_openai_chat(self):
        connector = OpenAIConnector({"api_key": "k", "model": "m"})

        async def create(**kwargs):
            message = SimpleNamespace(content="words", tool_calls=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=7,
                                      prompt_tokens_details=SimpleNamespace(cached_tokens=64)))

        connector.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        reply = run(connector.chat([{"role": "user", "content": "q"}]))
        assert (reply.usage.input, reply.usage.cache_read, reply.usage.output) == (36, 64, 7)

    def test_openai_chat_from_a_server_that_says_nothing(self):
        connector = OpenAIConnector({"api_key": "k", "model": "m"})

        async def create(**kwargs):
            message = SimpleNamespace(content="words", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        connector.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        assert run(connector.chat([{"role": "user", "content": "q"}])).usage == Usage()

    def test_openai_responses(self):
        seen = []
        connector = TestOpenAIResponses.connector(TestOpenAIResponses.answer(
            [TestOpenAIResponses.words("ready")],
            usage={"input_tokens": 500, "output_tokens": 60, "total_tokens": 560,
                   "input_tokens_details": {"cached_tokens": 384},
                   "output_tokens_details": {"reasoning_tokens": 48}}), seen)
        usage = run(connector.chat([{"role": "user", "content": "q"}])).usage
        assert (usage.input, usage.cache_read, usage.output, usage.reasoning) == (116, 384, 60, 48)

    def test_anthropic(self):
        connector = AnthropicConnector({"api_key": "k", "model": "m"})

        async def create(**kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
                usage=SimpleNamespace(input_tokens=12, output_tokens=3,
                                      cache_read_input_tokens=2048,
                                      cache_creation_input_tokens=0))

        connector.client = SimpleNamespace(messages=SimpleNamespace(create=create))
        usage = run(connector.chat([{"role": "user", "content": "q"}])).usage
        assert (usage.input, usage.cache_read, usage.output, usage.reported) == (12, 2048, 3, True)

    def test_gemini(self):
        def answer(request):
            return httpx.Response(200, json={
                "candidates": [{"content": {"role": "model", "parts": [{"text": "ready"}]},
                                "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 70, "cachedContentTokenCount": 64,
                                  "candidatesTokenCount": 2, "thoughtsTokenCount": 9}})
        usage = run(TestGemini().connector(answer, []).chat(
            [{"role": "user", "content": "q"}])).usage
        assert (usage.input, usage.cache_read, usage.output, usage.reasoning) == (6, 64, 11, 9)

    def test_bedrock(self):
        def answer(request):
            return httpx.Response(200, json={
                "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 8, "outputTokens": 2, "totalTokens": 10,
                          "cacheReadInputTokens": 1500, "cacheWriteInputTokens": 0}})
        usage = run(TestBedrock().connector(answer, []).chat(
            [{"role": "user", "content": "q"}])).usage
        assert (usage.input, usage.cache_read, usage.output) == (8, 1500, 2)

    def test_openai_embeddings(self):
        async def create(**kwargs):
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])],
                                   usage=SimpleNamespace(prompt_tokens=11, total_tokens=11))

        connector = OpenAIConnector({"api_key": "k", "model": "text-embedding-3-small"})
        connector.client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
        vectors = run(connector.embed(["a"]))
        assert vectors == [[0.1]] and isinstance(vectors, Embeddings)
        assert (vectors.usage.input, vectors.usage.output) == (11, 0)

    def test_vertex_embeddings_add_up_each_texts_tokens(self):
        from ai_runtime.llms.connector.vertex import VertexGeminiConnector

        def handler(request):
            return httpx.Response(200, json={"predictions": [
                {"embeddings": {"values": [0.1], "statistics": {"token_count": 4}}},
                {"embeddings": {"values": [0.2], "statistics": {"token_count": 6}}}]})

        connector = VertexGeminiConnector({
            "model": "gemini-embedding-001",
            "endpoint": "https://us-central1-aiplatform.googleapis.com",
            "api_key": service_account_key()})
        connector.account.token = lambda: "t"
        connector._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
        vectors = run(connector.embed(["a", "b"]))
        assert vectors == [[0.1], [0.2]] and vectors.usage.input == 10

    def test_gemini_embeddings_say_nothing_of_tokens(self):
        def answer(request):
            return httpx.Response(200, json={"embeddings": [{"values": [0.5]}]})
        vectors = run(TestGemini().connector(answer, [], model="gemini-embedding-001")
                      .embed(["a"]))
        assert vectors == [[0.5]] and not vectors.usage.reported


# ======================================================================
# The meter: written down, sent later, never in the way
# ======================================================================

class Model:
    model = "claude-test"

    def __init__(self, usage=None):
        self.reply = ModelReply(prose="ok", usage=usage or Usage(input=10, output=2, reported=True))


CONNECTION = {"secret_ref": "llm_abc", "provider": "anthropic", "model": "claude-test",
              "api_key": "sk-never-sent"}


class TestTheMeter:
    def test_a_call_is_written_down_with_its_connection_and_never_its_key(self):
        sent = []

        async def send(calls):
            sent.append(list(calls))

        meter = Meter(send=send, connection=lambda: CONNECTION)
        meter.record("assistant", Model(), Model().reply, time.monotonic() - 0.25)
        assert sent == []                      # held, not sent at once
        run(meter.flush())
        (call,) = sent[0]
        assert call["purpose"] == "assistant"
        assert (call["connection_id"], call["provider"], call["model"]) == (
            "llm_abc", "anthropic", "claude-test")
        assert (call["input"], call["output"], call["reported"]) == (10, 2, True)
        assert 200 <= call["duration_ms"] < 5000
        assert abs(call["at"] - time.time()) < 5
        assert "sk-never-sent" not in json.dumps(call)
        assert meter.held == []

    def test_an_agents_call_is_in_its_name(self):
        meter = Meter()
        token = ON_BEHALF.set({"agent": "agt_notes", "function": "notebook.note.save"})
        try:
            meter.record("agent", Model(), Model().reply, time.monotonic())
        finally:
            ON_BEHALF.reset(token)
        meter.record("agent", Model(), Model().reply, time.monotonic())
        named, unnamed = meter.held
        assert (named["agent"], named["function"]) == ("agt_notes", "notebook.note.save")
        assert "agent" not in unnamed

    def test_a_purpose_it_does_not_know_is_not_recorded_and_nothing_breaks(self, caplog):
        meter = Meter()
        with caplog.at_level(logging.WARNING):
            meter.record("gossip", Model(), Model().reply, time.monotonic())
        assert meter.held == [] and "not recorded" in caplog.text

    def test_a_reply_that_says_nothing_is_still_a_call(self):
        meter = Meter()
        meter.record("title", SimpleNamespace(), "a plain string", time.monotonic())
        (call,) = meter.held
        assert (call["input"], call["output"], call["reported"]) == (0, 0, False)

    def test_it_is_sent_a_batch_at_a_time(self):
        sent = []

        async def send(calls):
            sent.append(len(calls))

        meter = Meter(send=send)
        meter.SEND_AT = 10 ** 6
        for _ in range(Meter.BATCH * 2 + 3):
            meter.record("assistant", Model(), Model().reply, time.monotonic())
        assert run(meter.flush()) == Meter.BATCH * 2 + 3
        assert sent == [Meter.BATCH, Meter.BATCH, 3]

    def test_a_send_that_fails_is_logged_and_let_go(self, caplog):
        async def send(calls):
            raise RuntimeError("platform down")

        meter = Meter(send=send)
        meter.record("assistant", Model(), Model().reply, time.monotonic())
        with caplog.at_level(logging.WARNING):
            assert run(meter.flush()) == 0
        assert "not recorded: platform down" in caplog.text
        assert meter.held == []

    def test_enough_held_is_sent_without_being_asked_in_the_background(self):
        sent = []

        async def send(calls):
            await asyncio.sleep(0)
            sent.append(len(calls))

        async def scenario():
            meter = Meter(send=send)
            for _ in range(Meter.SEND_AT):
                meter.record("assistant", Model(), Model().reply, time.monotonic())
            assert sent == []                  # the caller did not wait
            await meter._sending
            return meter

        meter = run(scenario())
        assert sent == [Meter.SEND_AT] and meter.held == []

    def test_what_is_held_is_bounded(self):
        meter = Meter()
        meter.HELD_MAX = 5
        for n in range(8):
            meter.record("assistant", Model(Usage(input=n, reported=True)),
                         Model(Usage(input=n, reported=True)).reply, time.monotonic())
        assert [call["input"] for call in meter.held] == [3, 4, 5, 6, 7]


# ======================================================================
# A chat's calls reach the platform
# ======================================================================

class TestAChatsCalls:
    def build(self, script, services=None):
        from ai_runtime.tests.test_session import build

        session, services = build(script, services)
        session.llm_config = dict(CONNECTION, provider="fake", model="")
        return session, services

    @staticmethod
    async def sent(services, count, timeout=5.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while len(services.usage) < count:
            assert asyncio.get_running_loop().time() < deadline, services.usage
            await asyncio.sleep(0.02)

    def test_a_turns_calls_are_sent_when_it_rests(self):
        from ai_runtime.tests.test_session import action

        session, services = self.build([
            action(action="say", text="Hello."), action(action="finish")])

        async def scenario():
            await session.open()
            await session.deliver_user("hi")
            await session.wait_idle()
            await self.sent(services, 2)

        run(scenario())
        assert [call["purpose"] for call in services.usage] == ["assistant", "assistant"]
        assert {call["chat_id"] for call in services.usage} == {"chat_1"}
        assert {call["connection_id"] for call in services.usage} == {"llm_abc"}
        assert {call["provider"] for call in services.usage} == {"fake"}

    def test_an_agents_model_call_is_recorded_in_its_name(self):
        from ai_runtime.execution.call import Call

        session, services = self.build(["A summary."])

        async def scenario():
            await session.open()
            call = Call("call_1", agent=SimpleNamespace(agent_id="agt_llm"),
                        function_spec={"llm": True}, canonical_name="llm.text.summarize",
                        sinks=session.assistant.sinks)
            answer = await call.llm([{"role": "user", "content": "text"}])
            assert answer["text"] == "A summary."
            await session.usage.flush()

        run(scenario())
        (call,) = services.usage
        assert (call["purpose"], call["agent"], call["function"]) == (
            "agent", "agt_llm", "llm.text.summarize")

    def test_a_platform_that_will_not_take_them_does_not_stop_the_chat(self, caplog):
        from ai_runtime.tests.test_session import action
        from sim.session_services import SimSessionServices

        class Refusing(SimSessionServices):
            async def record_usage(self, chat_id, calls):
                raise RuntimeError("AI:Usage:Record failed (403)")

        session, services = self.build([
            action(action="say", text="Hello."), action(action="finish"),
            action(action="say", text="Again."), action(action="finish")], Refusing())

        async def scenario():
            await session.open()
            await session.deliver_user("hi")
            await session.wait_idle()
            await session.deliver_user("again")
            await session.wait_idle()
            await asyncio.sleep(0.1)

        with caplog.at_level(logging.WARNING):
            run(scenario())
        said = [m["parts"][0]["content"] for m in services.messages["chat_1"]
                if m["actor"] == "ai"]
        assert said == ["Hello.", "Again."]
        assert "not recorded: AI:Usage:Record failed" in caplog.text

    def test_a_helpers_calls_go_under_its_thread(self):
        from ai_runtime.tests.test_session import action

        session, services = self.build([
            action(action="spawn", goal="Say hello."),
            action(action="finish"),
            # the helper
            action(action="finish", summary="Said hello."),
            # the parent, woken by the report
            action(action="say", text="Done."),
            action(action="finish"),
        ])

        async def scenario():
            await session.open()
            await session.deliver_user("get a helper to say hello")
            deadline = asyncio.get_running_loop().time() + 20
            while not any(call["thread"] for call in services.usage):
                assert asyncio.get_running_loop().time() < deadline, services.usage
                await asyncio.sleep(0.05)
            await session.wait_idle()

        run(scenario())
        threads = {call["thread"] for call in services.usage}
        helper = next(thread for thread in threads if thread)
        assert helper.startswith("sub_")
        assert {call["chat_id"] for call in services.usage} == {"chat_1"}
        assert all(call["connection_id"] == "llm_abc" for call in services.usage)


class TestRouting:
    def test_the_embedding_and_the_rerank_are_the_chats(self, tmp_path):
        from ai_runtime.llms.connector.fake import FakeConnector
        from ai_runtime.mind.router import AgentRouter
        from ai_runtime.tests.test_agent_router import ROSTER

        embedding = {"provider": "fake", "model": "embed-test", "secret_ref": "llm_emb"}
        router = AgentRouter(tmp_path, factory=lambda config: FakeConnector([]))
        reranker = FakeConnector(['["' + next(iter(ROSTER)) + '"]'])
        reranker.reranks = True
        meter = Meter()
        run(router.shortlist(ROSTER, "send a mail", [], {
            "embedding": embedding, "threshold": 1, "shortlist": 1},
            reranker=reranker, meter=meter))
        purposes = [call["purpose"] for call in meter.held]
        assert purposes.count("embed") >= 2            # the index, then the words
        assert purposes[-1] == "rerank"
        embeds = [call for call in meter.held if call["purpose"] == "embed"]
        assert {call["connection_id"] for call in embeds} == {"llm_emb"}
