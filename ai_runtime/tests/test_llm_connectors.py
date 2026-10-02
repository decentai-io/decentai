"""The config-pure LLM connector package. No network, no environment."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from ai_runtime.llms import (
    AnthropicConnector,
    FakeConnector,
    LLMConnectorFactory,
    OpenAIConnector,
)


def run(coro):
    return asyncio.run(coro)


class TestFactory:
    def test_creates_the_right_connector_per_provider(self):
        openai = LLMConnectorFactory.create(
            {"provider": "openai", "api_key": "k", "model": "m"}
        )
        anthropic = LLMConnectorFactory.create(
            {"provider": "Anthropic", "api_key": "k", "model": "m"}
        )
        assert isinstance(openai, OpenAIConnector)
        assert isinstance(anthropic, AnthropicConnector)

    def test_a_provider_with_no_connector_is_rejected(self):
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            LLMConnectorFactory.create({"provider": "azure_openai"})

    def test_unknown_provider_is_rejected(self):
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            LLMConnectorFactory.create({"provider": "watson"})
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            LLMConnectorFactory.create({})

    def test_missing_required_config_is_rejected(self):
        with pytest.raises(ValueError, match="api_key"):
            LLMConnectorFactory.create({"provider": "openai", "model": "m"})
        with pytest.raises(ValueError, match="model"):
            LLMConnectorFactory.create({"provider": "openai", "api_key": "k"})
        with pytest.raises(ValueError, match="model"):
            LLMConnectorFactory.create({"provider": "anthropic", "api_key": "k"})


class TestPictures:
    """Two providers, two wire formats. They are not interchangeable,
    which is why each connector spells its own — and why having the
    method at all is what says a model may be shown one."""

    def test_each_connector_shapes_a_picture_its_own_way(self):
        openai = OpenAIConnector({"api_key": "k", "model": "m"})
        anthropic = AnthropicConnector({"api_key": "k", "model": "m"})

        assert openai.image_block("image/png", "QUJD") == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,QUJD"},
        }
        assert anthropic.image_block("image/png", "QUJD") == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": "QUJD"},
        }

    def test_a_system_message_of_blocks_keeps_only_its_words(self):
        """Anthropic takes the system prompt as a string. str() of a
        block list would ship a Python repr — and a picture's base64
        with it."""
        system, converted = AnthropicConnector.split_messages([
            {"role": "system", "content": [
                {"type": "text", "text": "You are the assistant."},
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64,SECRETBYTES"}},
            ]},
            {"role": "user", "content": "hi"},
        ])
        assert system == "You are the assistant."
        assert "SECRETBYTES" not in system
        assert converted == [{"role": "user", "content": "hi"}]

    def test_a_refusal_is_recognised_however_it_is_worded(self):
        from ai_runtime.llms.connector.tools import is_image_refusal

        for said in ("400: unsupported content type 'image_url'",
                     "This model does not support image input",
                     "Vision is not enabled for this deployment"):
            assert is_image_refusal(RuntimeError(said)), said
        # And an ordinary failure is not mistaken for one.
        assert not is_image_refusal(RuntimeError("rate limit exceeded"))
        assert not is_image_refusal(RuntimeError("context length exceeded"))


class FakeCompletionClient:
    """Stands in for the OpenAI SDK client: records kwargs, returns text."""

    def __init__(self, text="hello"):
        self.kwargs = None

        async def create(**kwargs):
            self.kwargs = kwargs
            message = SimpleNamespace(content=text)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=create)
        )


class TestOpenAIEmbeddings:
    def test_embed_asks_the_embeddings_endpoint_with_the_connections_model(self):
        from types import SimpleNamespace

        seen = {}

        async def create(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2]),
                                         SimpleNamespace(embedding=[0.3, 0.4])])

        connector = OpenAIConnector({"api_key": "k", "model": "text-embedding-3-large"})
        connector.client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
        vectors = asyncio.run(connector.embed(["a", "b"]))
        assert vectors == [[0.1, 0.2], [0.3, 0.4]]
        assert seen == {"model": "text-embedding-3-large", "input": ["a", "b"]}

    def test_a_scripted_model_embeds_by_words(self):
        fake = FakeConnector([])
        a, b, c = asyncio.run(fake.embed(["send mail to dana", "send mail now", "book a room"]))
        from ai_runtime.reasoning.agent_router import AgentRouter
        assert AgentRouter.cosine(a, b) > AgentRouter.cosine(a, c)


class TestOpenAIChat:
    def test_chat_returns_text_and_passes_max_tokens(self):
        connector = OpenAIConnector(
            {"api_key": "k", "model": "m", "endpoint": "http://localhost:11434/v1"}
        )
        stub = FakeCompletionClient("the answer")
        connector.client = stub

        text = run(connector.chat([{"role": "user", "content": "q"}], max_tokens=99))
        assert text.content == "the answer"
        assert stub.kwargs["model"] == "m"
        assert stub.kwargs["max_tokens"] == 99

    def test_reasoning_effort_is_sent_only_when_the_connection_set_it(self):
        """A reasoning model thinks before every beat, and the connection
        says how hard; a model that does not reason is never handed the
        parameter, because it would refuse the whole request."""
        thinking = OpenAIConnector({"api_key": "k", "model": "m",
                                    "reasoning_effort": "Low"})
        stub = FakeCompletionClient("quick")
        thinking.client = stub
        run(thinking.chat([{"role": "user", "content": "q"}]))
        assert stub.kwargs["reasoning_effort"] == "low"

        plain = OpenAIConnector({"api_key": "k", "model": "m"})
        stub = FakeCompletionClient()
        plain.client = stub
        run(plain.chat([{"role": "user", "content": "q"}]))
        assert "reasoning_effort" not in stub.kwargs

    def test_a_server_that_wants_the_other_cap_name_is_asked_that_way_from_then_on(self):
        """Newer OpenAI models refuse ``max_tokens`` and want
        ``max_completion_tokens``; self-hosted servers often know only the
        old name. The first refusal teaches the connection, once."""
        connector = OpenAIConnector({"api_key": "k", "model": "m"})
        seen = []

        async def create(**kwargs):
            seen.append(kwargs)
            if "max_tokens" in kwargs:
                raise RuntimeError(
                    "Error code: 400 - Unsupported parameter: 'max_tokens' is not "
                    "supported with this model. Use 'max_completion_tokens' instead.")
            message = SimpleNamespace(content="capped")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        connector.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        reply = run(connector.chat([{"role": "user", "content": "q"}], max_tokens=50))
        assert reply.content == "capped"
        assert [sorted(k for k in call if k.startswith("max_")) for call in seen] == [
            ["max_tokens"], ["max_completion_tokens"]]
        assert seen[-1]["max_completion_tokens"] == 50

        run(connector.chat([{"role": "user", "content": "q"}], max_tokens=60))
        assert len(seen) == 3 and seen[-1]["max_completion_tokens"] == 60

    def test_max_tokens_omitted_when_not_given(self):
        connector = OpenAIConnector({"api_key": "k", "model": "m"})
        stub = FakeCompletionClient()
        connector.client = stub

        run(connector.chat([{"role": "user", "content": "q"}]))
        assert "max_tokens" not in stub.kwargs

class TestAnthropicChat:
    def test_system_messages_collapse_into_the_system_parameter(self):
        system, converted = AnthropicConnector.split_messages([
            {"role": "system", "content": "You are helpful."},
            {"role": "system", "content": "Context: notes."},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "tool", "content": "coerced"},
        ])
        assert system == "You are helpful.\n\nContext: notes."
        assert [m["role"] for m in converted] == ["user", "assistant", "user"]

    def test_chat_plumbs_system_and_default_max_tokens(self):
        connector = AnthropicConnector({"api_key": "k", "model": "m"})
        captured = {}

        async def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="claude says")]
            )

        connector.client = SimpleNamespace(
            messages=SimpleNamespace(create=create)
        )

        text = run(connector.chat([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
        ]))
        assert text.content == "claude says"
        assert captured["system"] == "sys"
        assert captured["max_tokens"] == AnthropicConnector.DEFAULT_MAX_TOKENS
        assert captured["messages"] == [{"role": "user", "content": "q"}]


class TestFakeConnector:
    def test_scripted_responses_in_order_and_calls_recorded(self):
        fake = FakeConnector(["first", "second"])

        async def scenario():
            one = await fake.chat([{"role": "user", "content": "a"}], max_tokens=5)
            two = await fake.chat([{"role": "user", "content": "b"}])
            return one, two

        one, two = run(scenario())
        assert (one.content, two.content) == ("first", "second")
        assert len(fake.calls) == 2
        assert fake.calls[0]["max_tokens"] == 5

    def test_exhausted_script_raises(self):
        fake = FakeConnector([])
        with pytest.raises(RuntimeError, match="script exhausted"):
            run(fake.chat([{"role": "user", "content": "a"}]))


class FakeToolClient:
    """The OpenAI SDK client answering with tool calls — or refusing a
    tool_choice, the way an OpenAI-compatible server that lacks it does."""

    def __init__(self, calls, text=None, refuse_required=False):
        self.kwargs = []
        outer = self

        async def create(**kwargs):
            outer.kwargs.append(kwargs)
            if refuse_required and kwargs.get("tool_choice") == "required":
                raise RuntimeError("400: tool_choice 'required' is not supported")
            tool_calls = [
                SimpleNamespace(function=SimpleNamespace(
                    name=name, arguments=arguments))
                for name, arguments in calls
            ] or None
            message = SimpleNamespace(content=text, tool_calls=tool_calls)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


TOOLS = [{"type": "function", "function": {
    "name": "say", "description": "d",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}}}}]


class TestTheReply:
    """One model call answered whole: the content the cycle reads, and
    beside it why it stopped."""

    def test_openai_reports_the_stop_reason(self):
        connector = OpenAIConnector({"api_key": "k", "model": "m"})

        async def create(**kwargs):
            message = SimpleNamespace(content="words", tool_calls=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message,
                                         finish_reason="stop")])

        connector.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        reply = run(connector.chat([{"role": "user", "content": "q"}]))
        assert reply.content == "words" and reply.prose == "words"
        assert reply.calls == []
        assert reply.stop_reason == "stop"

    def test_anthropic_reports_the_stop_reason(self):
        connector = AnthropicConnector({"api_key": "k", "model": "m"})

        async def create(**kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(type="tool_use", name="finish",
                                         input={})],
                stop_reason="tool_use")

        connector.client = SimpleNamespace(
            messages=SimpleNamespace(create=create))
        reply = run(connector.chat([{"role": "user", "content": "q"}],
                                   tools=TOOLS))
        assert reply.calls == [{"name": "finish", "arguments": {}}]
        assert json.loads(reply.content) == {"action": "finish"}
        assert reply.stop_reason == "tool_use"

    def test_a_reply_that_says_nothing_of_why_it_stopped(self):
        connector = OpenAIConnector({"api_key": "k", "model": "m"})
        connector.client = FakeCompletionClient("x")
        reply = run(connector.chat([{"role": "user", "content": "q"}]))
        assert reply.stop_reason == ""

    def test_anthropic_asks_a_model_for_no_more_than_it_allows(self):
        """Each model has its own ceiling on a reply. One that allows
        less than the standard says how much: it is asked again for
        that, and for no more from then on."""
        connector = AnthropicConnector({"api_key": "k", "model": "m"})
        asked = []

        async def create(**kwargs):
            asked.append(kwargs["max_tokens"])
            if kwargs["max_tokens"] > 8192:
                raise RuntimeError(
                    "max_tokens: 16384 > 8192, which is the maximum "
                    "allowed number of output tokens for m")
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")],
                stop_reason="end_turn")

        connector.client = SimpleNamespace(
            messages=SimpleNamespace(create=create))
        for _ in range(2):
            assert run(connector.chat(
                [{"role": "user", "content": "q"}])).content == "ok"
        assert asked == [16384, 8192, 8192]


class TestToolCalling:
    def test_a_tool_call_comes_back_as_the_actions_json(self):
        connector = OpenAIConnector({"api_key": "k", "model": "m"})
        stub = FakeToolClient([("say", '{"text": "Hello", "final": true}')])
        connector.client = stub

        reply = run(connector.chat([{"role": "user", "content": "q"}], tools=TOOLS))
        assert json.loads(reply.content) == {"action": "say", "text": "Hello", "final": True}
        assert stub.kwargs[0]["tools"] == TOOLS
        assert stub.kwargs[0]["tool_choice"] == "required"

    def test_several_calls_come_one_per_line_and_text_alone_stays_text(self):
        connector = OpenAIConnector({"api_key": "k", "model": "m"})
        connector.client = FakeToolClient([
            ("say", '{"text": "a"}'), ("finish", "{}")])
        reply = run(connector.chat([], tools=TOOLS))
        assert [json.loads(line) for line in reply.content.split("\n")] == [
            {"action": "say", "text": "a"}, {"action": "finish"}]

        connector.client = FakeToolClient([], text="just words")
        assert run(connector.chat([], tools=TOOLS)).content == "just words"

    def test_a_server_that_refuses_required_is_asked_with_auto_from_then_on(self):
        connector = OpenAIConnector({"api_key": "k", "model": "m"})
        stub = FakeToolClient([("finish", "{}")], refuse_required=True)
        connector.client = stub

        reply = run(connector.chat([], tools=TOOLS))
        assert json.loads(reply.content) == {"action": "finish"}
        assert [k["tool_choice"] for k in stub.kwargs] == ["required", "auto"]
        run(connector.chat([], tools=TOOLS))
        assert stub.kwargs[-1]["tool_choice"] == "auto"

    def test_without_tools_nothing_about_tools_is_sent(self):
        connector = OpenAIConnector({"api_key": "k", "model": "m"})
        stub = FakeToolClient([], text="plain")
        connector.client = stub
        assert run(connector.chat([{"role": "user", "content": "q"}])).content == "plain"
        assert "tools" not in stub.kwargs[0] and "tool_choice" not in stub.kwargs[0]

    def test_anthropic_takes_input_schema_and_answers_tool_use_blocks(self):
        connector = AnthropicConnector({"api_key": "k", "model": "m"})
        seen = {}

        async def create(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(content=[
                SimpleNamespace(type="tool_use", name="say",
                                input={"text": "Hi", "final": True}),
            ])

        connector.client = SimpleNamespace(messages=SimpleNamespace(create=create))
        reply = run(connector.chat([{"role": "user", "content": "q"}], tools=TOOLS))
        assert json.loads(reply.content) == {"action": "say", "text": "Hi", "final": True}
        assert seen["tools"] == [{"name": "say", "description": "d",
                                  "input_schema": TOOLS[0]["function"]["parameters"]}]
        assert seen["tool_choice"] == {"type": "any"}

    def test_the_fake_records_what_it_was_offered(self):
        connector = FakeConnector(["x"])
        run(connector.chat([], tools=TOOLS))
        assert connector.calls[0]["tools"] == TOOLS


@pytest.mark.parametrize("provider,endpoint", [
    ("openrouter", "https://openrouter.ai/api/v1"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ("deepseek", "https://api.deepseek.com"),
    ("groq", "https://api.groq.com/openai/v1"),
    ("mistral", "https://api.mistral.ai/v1"),
    ("xai", "https://api.x.ai/v1"),
    ("openai_compatible", "https://gateway.example.test/v1"),
])
def test_compatible_provider_request_and_tool_response(monkeypatch, provider, endpoint):
    import httpx
    from openai import AsyncOpenAI
    import ai_runtime.llms.connector.openai as module

    def respond(request):
        assert str(request.url) == endpoint.rstrip("/") + "/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "chosen-model"
        assert body["tool_choice"] == "required"
        assert body["tools"][0]["function"]["name"] == "lookup"
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 0,
            "model": "chosen-model", "choices": [{"index": 0,
                "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {
                    "name": "lookup", "arguments": '{"query":"hello"}'}}]}}],
        })

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            monkeypatch.setattr(module, "AsyncOpenAI", lambda **kw: AsyncOpenAI(http_client=client, **kw))
            connector = LLMConnectorFactory.create({"provider": provider, "endpoint": endpoint,
                "api_key": "test-key", "model": "chosen-model"})
            reply = await connector.chat([{"role": "user", "content": "Find hello"}], tools=[{
                "type": "function", "function": {"name": "lookup", "parameters": {
                    "type": "object", "properties": {"query": {"type": "string"}}}}}])
            assert reply.calls[0]["name"] == "lookup"
            assert json.loads(reply.calls[0]["arguments"]) == {"query": "hello"}
    run(exercise())
    with pytest.raises(ValueError, match="requires endpoint"):
        LLMConnectorFactory.create({"provider": provider, "api_key": "k", "model": "m"})
