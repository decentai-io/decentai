"""The config-pure LLM connector package. No network, no environment."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from ai_runtime.llms import (
    AnthropicConnector,
    BedrockConnector,
    FakeConnector,
    LLMConnectorFactory,
    OpenAIConnector,
)
from contracts.generate_llm_providers import ProviderCatalogWriter
from contracts.llm_providers import LlmProviders


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


class TestTheCatalog:
    """contracts/llm_providers.json: the one list of providers, read by
    the backend, the page and this factory."""

    def test_every_provider_in_the_catalog_gets_the_connector_of_its_protocol(self):
        speaks = {"openai": OpenAIConnector, "anthropic": AnthropicConnector,
                  "bedrock": BedrockConnector}
        assert set(speaks) == set(LlmProviders.PROTOCOLS)
        for entry in LlmProviders.all():
            filled = entry["endpoint"].replace("<", "").replace(">", "")
            connector = LLMConnectorFactory.create({
                "provider": entry["id"], "api_key": "k", "model": "m",
                "endpoint": filled or "https://gateway.example.test/v1",
            })
            assert type(connector) is speaks[entry["protocol"]], entry["id"]

    def test_every_entry_is_a_lowercase_id_a_name_and_a_public_https_address(self):
        entries = LlmProviders.all()
        ids = [entry["id"] for entry in entries]
        assert len(ids) == len(set(ids))
        for entry in entries:
            assert entry["id"] == entry["id"].strip().lower() and entry["name"]
            assert entry["protocol"] in LlmProviders.PROTOCOLS
            if entry["id"] != "openai_compatible":
                assert entry["endpoint"].startswith("https://"), entry["id"]
                assert "${" not in entry["endpoint"], entry["id"]

    def test_an_address_with_a_blank_left_in_it_is_refused_not_tried(self):
        # <aws-region> is the person's to fill in; a request to an
        # address that still carries it could only fail somewhere less
        # clear than here.
        bedrock = LlmProviders.find("amazon-bedrock")
        assert LlmProviders.unfilled(bedrock["endpoint"])
        with pytest.raises(ValueError, match="blank to fill in"):
            LLMConnectorFactory.create({
                "provider": "amazon-bedrock", "api_key": "k", "model": "m",
                "endpoint": bedrock["endpoint"]})
        assert not LlmProviders.unfilled("https://bedrock-runtime.eu-west-1.amazonaws.com")

    def test_only_the_custom_entry_leaves_its_address_to_the_person(self):
        blank = [entry["id"] for entry in LlmProviders.all() if not entry["endpoint"]]
        assert blank == ["openai_compatible"]

    def test_the_providers_named_before_the_catalog_kept_their_ids_and_addresses(self):
        # A connection stores its provider's id: one renamed here would
        # be a saved connection the runtime no longer has a connector for.
        before = {
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com",
            "openrouter": "https://openrouter.ai/api/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "deepseek": "https://api.deepseek.com",
            "groq": "https://api.groq.com/openai/v1",
            "mistral": "https://api.mistral.ai/v1",
            "xai": "https://api.x.ai/v1",
            "openai_compatible": "",
        }
        for provider, endpoint in before.items():
            assert LlmProviders.find(provider)["endpoint"] == endpoint

    def test_an_anthropic_address_carries_no_version_path(self):
        # The Anthropic client adds /v1 itself; an address ending in it
        # would be asked for /v1/v1/messages.
        for entry in LlmProviders.all():
            if entry["protocol"] == "anthropic":
                assert not entry["endpoint"].rstrip("/").endswith("/v1"), entry["id"]

    def test_a_providers_models_are_offered_by_its_own_ids_for_them(self):
        bedrock = LlmProviders.models("amazon-bedrock")
        assert {"id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "name": "Claude Haiku 4.5 (US)"} in bedrock
        assert all(set(model) == {"id", "name"} and model["id"] and model["name"]
                   for model in bedrock)
        # The list is kept under the id connections store, not models.dev's.
        assert LlmProviders.models("gemini") and not LlmProviders.models("google")
        # An offer, never a gate: a provider with none listed is still one.
        assert LlmProviders.models("openai_compatible") == []
        assert LlmProviders.models("nobody") == []

    def test_the_scripted_connector_is_reachable_and_in_no_catalog(self):
        assert LlmProviders.find("fake") is None
        assert isinstance(
            LLMConnectorFactory.create({"provider": "fake"}), FakeConnector)


class TestWritingTheCatalog:
    """contracts/generate_llm_providers.py, over a models.dev checkout
    small enough to read here."""

    @staticmethod
    def checkout(tmp_path, **providers):
        for provider_id, toml in providers.items():
            folder = tmp_path / "providers" / provider_id.replace("_", "-")
            folder.mkdir(parents=True)
            (folder / "provider.toml").write_text(toml, encoding="utf-8")
        return {entry["id"]: entry
                for entry in ProviderCatalogWriter(tmp_path).providers()}

    def test_a_provider_that_speaks_openais_protocol_is_kept_with_its_address(self, tmp_path):
        kept = self.checkout(tmp_path, acme=(
            'name = "Acme"\nnpm = "@ai-sdk/openai-compatible"\n'
            'api = "https://api.acme.example/v1"\n'))
        assert kept["acme"] == {
            "id": "acme", "name": "Acme", "protocol": "openai",
            "endpoint": "https://api.acme.example/v1"}

    def test_an_anthropic_address_loses_the_version_the_client_adds(self, tmp_path):
        kept = self.checkout(tmp_path, acme=(
            'name = "Acme"\nnpm = "@ai-sdk/anthropic"\n'
            'api = "https://api.acme.example/anthropic/v1"\n'))
        assert kept["acme"]["protocol"] == "anthropic"
        assert kept["acme"]["endpoint"] == "https://api.acme.example/anthropic"

    def test_what_differs_per_customer_is_kept_as_a_blank_to_fill_in(self, tmp_path):
        kept = self.checkout(tmp_path, acme=(
            'name = "Acme"\nnpm = "@ai-sdk/openai-compatible"\n'
            'api = "https://${ACME_ACCOUNT_ID}.acme.example/v1"\n'))
        assert kept["acme"]["endpoint"] == "https://<acme-account-id>.acme.example/v1"

    def test_bedrock_names_its_own_protocol_and_leaves_the_region_blank(self, tmp_path):
        kept = self.checkout(
            tmp_path, amazon_bedrock='name = "Amazon Bedrock"\nnpm = "@ai-sdk/amazon-bedrock"\n')
        assert kept["amazon-bedrock"] == {
            "id": "amazon-bedrock", "name": "Amazon Bedrock", "protocol": "bedrock",
            "endpoint": "https://bedrock-runtime.<aws-region>.amazonaws.com"}

    def test_what_no_connector_reaches_as_it_is_is_left_out(self, tmp_path):
        kept = self.checkout(
            tmp_path,
            own_protocol='name = "A"\nnpm = "@ai-sdk/google-vertex"\n',
            no_scheme=('name = "B"\nnpm = "@ai-sdk/openai-compatible"\n'
                       'api = "${GATEWAY_BASE_URL}/v1"\n'),
            this_machine=('name = "C"\nnpm = "@ai-sdk/openai-compatible"\n'
                          'api = "http://127.0.0.1:1234/v1"\n'),
            github_copilot=('name = "D"\nnpm = "@ai-sdk/openai-compatible"\n'
                            'api = "https://api.githubcopilot.com"\n'),
        )
        assert list(kept) == ["openai_compatible"]

    def test_google_keeps_the_id_connections_were_saved_under(self, tmp_path):
        kept = self.checkout(tmp_path, google='name = "Google"\nnpm = "@ai-sdk/google"\n')
        assert "google" not in kept
        assert kept["gemini"]["name"] == "Google Gemini"

    def test_models_are_listed_by_name_under_the_id_the_catalog_gave_the_provider(self, tmp_path):
        self.checkout(tmp_path, google='name = "Google"\nnpm = "@ai-sdk/google"\n')
        models = tmp_path / "providers" / "google" / "models"
        (models / "vendor").mkdir(parents=True)
        (models / "zed.toml").write_text('name = "Zed"\n', encoding="utf-8")
        (models / "vendor" / "alpha-1.toml").write_text(
            'base_model = "vendor/alpha"\n', encoding="utf-8")
        (models / "old.toml").write_text(
            'name = "Old"\nstatus = "deprecated"\n', encoding="utf-8")
        (models / "bare.toml").write_text("", encoding="utf-8")
        (tmp_path / "models" / "vendor").mkdir(parents=True)
        (tmp_path / "models" / "vendor" / "alpha.toml").write_text(
            'name = "Alpha"\n', encoding="utf-8")
        assert ProviderCatalogWriter(tmp_path).models() == {"gemini": [
            ["vendor/alpha-1", "Alpha"], ["bare", "bare"], ["zed", "Zed"]]}

    def test_the_custom_entry_comes_last_after_the_names_in_order(self, tmp_path):
        kept = self.checkout(
            tmp_path,
            zeta='name = "Zeta"\nnpm = "@ai-sdk/openai-compatible"\napi = "https://z.example/v1"\n',
            alpha='name = "alpha"\nnpm = "@ai-sdk/openai-compatible"\napi = "https://a.example/v1"\n',
        )
        assert list(kept) == ["alpha", "zeta", "openai_compatible"]

    def test_the_file_in_the_repository_is_what_the_writer_writes(self):
        # Edited by hand, the catalog would say something models.dev
        # and the writer's own tables do not.
        import json
        document = json.loads(LlmProviders.PATH.read_text(encoding="utf-8"))
        assert set(document) == {"schema_version", "source", "providers"}
        assert len(document["source"]["commit"]) == 40
        assert document["providers"][-1] == ProviderCatalogWriter.CUSTOM
        names = [entry["name"].casefold() for entry in document["providers"][:-1]]
        assert names == sorted(names)


class TestBedrock:
    """Converse: Bedrock's own request shape, with a Bedrock API key as
    the bearer."""

    ENDPOINT = "https://bedrock-runtime.eu-west-1.amazonaws.com"
    MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

    def connector(self, respond, seen):
        import httpx

        def handler(request):
            seen.append(request)
            return respond(request)

        connector = LLMConnectorFactory.create({
            "provider": "amazon-bedrock", "endpoint": self.ENDPOINT + "/",
            "api_key": "bedrock-api-key-test", "model": self.MODEL})
        connector._client = lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler))
        return connector

    @staticmethod
    def answer(content, stop="end_turn"):
        import httpx
        return lambda request: httpx.Response(200, json={
            "output": {"message": {"role": "assistant", "content": content}},
            "stopReason": stop})

    def test_the_model_is_in_the_address_and_the_key_is_the_bearer(self):
        seen = []
        connector = self.connector(self.answer([{"text": "ready"}]), seen)
        reply = run(connector.chat([
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Say ready."}], max_tokens=64))
        request = seen[0]
        assert str(request.url) == (
            self.ENDPOINT + "/model/us.anthropic.claude-sonnet-4-5-20250929-v1%3A0/converse")
        assert request.headers["authorization"] == "Bearer bedrock-api-key-test"
        body = json.loads(request.content)
        assert body["system"] == [{"text": "Be brief."}]
        assert body["messages"] == [{"role": "user", "content": [{"text": "Say ready."}]}]
        assert body["inferenceConfig"] == {"maxTokens": 64}
        assert "toolConfig" not in body
        assert reply.content == "ready" and reply.stop_reason == "end_turn"

    def test_an_arn_stays_one_segment_of_the_address(self):
        seen = []
        connector = self.connector(self.answer([{"text": "ok"}]), seen)
        connector.model = "arn:aws:bedrock:eu-west-1:1:inference-profile/eu.model"
        run(connector.chat([{"role": "user", "content": "hi"}]))
        assert seen[0].url.raw_path.decode().endswith(
            "/model/arn%3Aaws%3Abedrock%3Aeu-west-1%3A1%3Ainference-profile%2Feu.model/converse")

    def test_tools_go_as_tool_specs_and_a_tool_use_comes_back_as_the_action(self):
        seen = []
        connector = self.connector(self.answer(
            [{"text": "Looking."},
             {"toolUse": {"toolUseId": "t1", "name": "lookup", "input": {"query": "hello"}}}],
            stop="tool_use"), seen)
        reply = run(connector.chat([{"role": "user", "content": "Find hello"}], tools=[{
            "type": "function", "function": {"name": "lookup", "description": "Look up.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}]))
        body = json.loads(seen[0].content)
        assert body["toolConfig"] == {
            "toolChoice": {"any": {}},
            "tools": [{"toolSpec": {"name": "lookup", "description": "Look up.",
                "inputSchema": {"json": {"type": "object",
                                         "properties": {"query": {"type": "string"}}}}}}]}
        assert reply.calls == [{"name": "lookup", "arguments": {"query": "hello"}}]
        assert json.loads(reply.content) == {"action": "lookup", "query": "hello"}

    def test_two_turns_of_one_role_reach_converse_as_one(self):
        # The transcript carries observations as user turns after user
        # turns; Converse refuses a conversation that does not alternate.
        system, messages = BedrockConnector.split_messages([
            {"role": "system", "content": "A"},
            {"role": "user", "content": "one"},
            {"role": "user", "content": [{"type": "text", "text": "two"}]},
            {"role": "assistant", "content": "three"},
            {"role": "tool", "content": "four"},
            {"role": "user", "content": ""},
        ])
        assert system == [{"text": "A"}]
        assert messages == [
            {"role": "user", "content": [{"text": "one"}, {"text": "two"}]},
            {"role": "assistant", "content": [{"text": "three"}]},
            {"role": "user", "content": [{"text": "four"}]},
        ]

    def test_a_picture_is_named_by_its_format_and_travels_as_it_was_made(self):
        block = BedrockConnector.image_block("image/jpeg", "QUJD")
        assert block == {"image": {"format": "jpeg", "source": {"bytes": "QUJD"}}}
        _, messages = BedrockConnector.split_messages([
            {"role": "user", "content": [{"type": "text", "text": "What is this?"}, block]}])
        assert messages[0]["content"] == [{"text": "What is this?"}, block]

    def test_a_refusal_is_raised_in_bedrocks_own_words(self):
        import httpx
        connector = self.connector(lambda request: httpx.Response(
            400, json={"message": "Input is too long for requested model."}), [])
        from ai_runtime.llms.connector.tools import is_context_overflow
        with pytest.raises(Exception, match="Bedrock answered 400") as refused:
            run(connector.chat([{"role": "user", "content": "hi"}]))
        assert is_context_overflow(refused.value)

    def test_a_reply_cut_at_the_cap_says_so_in_the_word_the_cycle_knows(self):
        connector = self.connector(self.answer([{"text": "half"}], stop="max_tokens"), [])
        assert run(connector.chat([{"role": "user", "content": "hi"}])).stop_reason == "max_tokens"

    def test_key_model_and_endpoint_are_all_required(self):
        for missing in ("api_key", "model", "endpoint"):
            config = {"provider": "amazon-bedrock", "api_key": "k", "model": "m",
                      "endpoint": self.ENDPOINT}
            config.pop(missing)
            with pytest.raises(ValueError, match=missing):
                LLMConnectorFactory.create(config)


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
