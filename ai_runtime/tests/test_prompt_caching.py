"""Prompt caching: a beat's request is laid out so a provider's cache
keeps as much of it as it can, and says where (docs/system/assistant.md,
"The frame").

What is pinned: the frame is ordered by how often its parts change and
holds no plan; the plan travels after the conversation, on every call,
and is never kept; the assistant's hints mark the frame's stable front
and the end of the transcript; Anthropic (and Claude on Vertex) turn
them into at most four ``cache_control`` marks, Bedrock into
``cachePoint`` blocks for the models that cache, and every other
connector sends none of them; OpenAI's own API is told which chat a
request is of by a hash; and a picture is the same bytes on every
call. The order of the tools is test_tool_order.py's.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from ai_runtime.chat.summarizer import Summarizer
from ai_runtime.door.host import SessionHost
from ai_runtime.llms.connector import cache
from ai_runtime.llms.connector.anthropic import AnthropicConnector
from ai_runtime.llms.connector.bedrock import BedrockConnector
from ai_runtime.llms.connector.fake import FakeConnector
from ai_runtime.llms.connector.gemini import GeminiConnector
from ai_runtime.llms.connector.openai import OpenAIConnector
from ai_runtime.llms.connector.responses import OpenAIResponsesConnector
from ai_runtime.llms.connector.vertex import VertexAnthropicConnector
from ai_runtime.llms.factory import LLMConnectorFactory
from ai_runtime.mind import frame
from ai_runtime.mind.state import AssistantState
from ai_runtime.tests.test_assistant import Harness, action, run
from ai_runtime.tests.test_fold import SECTIONED
from ai_runtime.tests.test_session import build
from ai_runtime.tests.test_sub_assistants import child_of

RULES = "Never write to the Finance notebook."
STABLE = "You are the assistant. " * 10
REST = "SKILLS\n(none written yet)\n\nAGENTS\nnotebook — Notebook"
TOOLS = [{"type": "function", "function": {
    "name": name, "description": name,
    "parameters": {"type": "object", "properties": {}}}}
    for name in ("say", "finish", "notebook__note__save")]


def request(*history, plan="PLAN\n(no plan yet)"):
    """A beat's request as the assistant hands it to a connector."""
    messages = [{"role": "system", "content": f"{STABLE}\n\n{REST}",
                 cache.STABLE: len(STABLE)}]
    messages += [dict(m) for m in history]
    messages[-1][cache.THROUGH] = True
    return messages + [{"role": "user", "content": plan, cache.TRANSIENT: True}]


HISTORY = (
    {"role": "user", "content": "[2026-10-17 09:00] save a note"},
    {"role": "assistant", "content": '{"action": "open_agent", "agent": "notebook"}'},
    {"role": "user", "content": "OBSERVATION:\n{\"agent\": \"notebook\"}"},
)


def no_hint_in(sent) -> None:
    text = json.dumps(sent, default=str)
    assert not any(hint in text for hint in cache.HINTS)


def alternating(messages) -> bool:
    roles = [m["role"] for m in messages if m["role"] != "system"]
    return all(a != b for a, b in zip(roles, roles[1:]))


def marks(value) -> int:
    return json.dumps(value, default=str).count('"cache_control"')


# ----------------------------------------------------------------------
# The frame
# ----------------------------------------------------------------------

class TestTheFrame:
    def test_its_parts_go_from_the_least_to_the_most_often_changed(self):
        state = AssistantState()
        state.summary = "Earlier: a note was saved."
        prompt = frame.system_prompt(state, {}, None, [], 40, ["kept"],
                                     guardrail=RULES)
        stable = frame.stable_prompt(RULES)
        assert prompt.startswith(stable) and stable.endswith(RULES)
        order = [prompt.index(block) for block in (
            "ORGANIZATION'S OWN RULES", "\nSKILLS\n", "\nMEMORY\n",
            "\nCONVERSATION SO FAR\n", "\nAGENTS\n")]
        assert order == sorted(order)

    def test_the_plan_is_not_in_it(self):
        state = AssistantState()
        state.plan.replace(["Save the note"])
        prompt = frame.system_prompt(state, {}, None, [], 40, [])
        assert "\nPLAN\n" not in prompt and "Save the note" not in prompt
        assert "Save the note" in frame.plan_message(state)["content"]


class TestTheBeat:
    def harness(self, script, **kwargs):
        return Harness(script, guardrail=RULES, **kwargs)

    def test_a_plan_change_leaves_the_frame_as_it_was(self):
        harness = self.harness([
            action(action="plan", steps=["Save the note", "Confirm"]),
            action(action="plan", item="w1", status="done"),
            action(action="finish", reason="awaiting_user"),
        ])
        run(harness.user("do two things").assistant.run())
        frames = {call["messages"][0]["content"]
                  for call in harness.connector.calls}
        assert len(frames) == 1
        assert harness.state.messages[0]["content"] in frames

    def test_every_call_ends_with_the_plan_as_it_stands_then(self):
        harness = self.harness([
            action(action="plan", steps=["Save the note"]),
            action(action="plan", item="w1", status="done"),
            action(action="finish", reason="awaiting_user"),
        ])
        run(harness.user("save it").assistant.run())
        plans = [call["messages"][-1] for call in harness.connector.calls]
        assert all(p["role"] == "user" and p["content"].startswith("PLAN\n")
                   for p in plans)
        assert "(no plan yet)" in plans[0]["content"]
        assert "w1 [still to do] Save the note" in plans[1]["content"]
        assert "w1 [done" in plans[2]["content"]

    def test_the_plan_message_is_never_kept(self):
        harness = self.harness([
            action(action="plan", steps=["Save the note"]),
            action(action="finish", reason="awaiting_user"),
        ])
        run(harness.user("save it").assistant.run())
        kept = json.dumps([harness.state.to_dict(), *harness.saved_states])
        assert "w1 [still to do]" not in kept
        assert "the list the user sees" not in kept
        assert not any(str(m.get("content", "")).startswith("PLAN\n")
                       for m in harness.state.messages)
        assert not any(hint in m for m in harness.state.messages
                       for hint in cache.HINTS)

    def test_the_hints_mark_the_stable_front_and_the_end_of_the_transcript(self):
        harness = self.harness([action(action="finish")])
        run(harness.user("hi").assistant.run())
        sent = harness.connector.calls[0]["messages"]
        stable = frame.stable_prompt(RULES)
        assert sent[0][cache.STABLE] == len(stable)
        assert sent[0]["content"].startswith(stable)
        assert sent[-2][cache.THROUGH] is True        # the person's line
        assert "hi" in sent[-2]["content"]
        assert cache.THROUGH not in sent[-1]          # the plan, after it
        assert sent[-1][cache.TRANSIENT] is True
        assert sum(cache.THROUGH in m for m in sent) == 1

    def test_a_caller_with_no_hints_is_passed_through_untouched(self):
        said = [{"role": "system", "content": "s"},
                {"role": "user", "content": "a"},
                {"role": "user", "content": "b"}]
        assert cache.plain(said) == said
        assert all(a is b for a, b in zip(cache.plain(said), said))
        assert not cache.hinted(said)

    def test_a_frame_saved_under_other_rules_is_not_called_stable(self):
        harness = self.harness([action(action="finish")])
        harness.state.messages = [
            {"role": "system", "content": "an old frame\n\nPLAN\n(no plan yet)"},
            {"role": "user", "content": "hi"}]
        sent = run(harness.assistant._request())
        assert cache.STABLE not in sent[0]
        # Reframed, as a rehydrated mind is: the hint is back.
        harness.assistant.reframe()
        sent = run(harness.assistant._request())
        assert sent[0][cache.STABLE] == len(frame.stable_prompt(RULES))
        assert "\nPLAN\n" not in sent[0]["content"]

    def test_a_fold_keeps_the_frame_first_and_the_plan_last(self):
        harness = self.harness([action(action="finish")])
        run(harness.user("hi").assistant.run())
        harness.state.plan.replace(["Keep going"])
        for n in range(8):
            harness.state.messages += [
                {"role": "assistant", "content": '{"action": "invoke"}'},
                {"role": "user", "content": "OBSERVATION:\n" + "x" * 4000}]
        folded = run(Summarizer(FakeConnector([SECTIONED])).maintain(
            harness.assistant, force=True))
        assert folded
        sent = run(harness.assistant._request())
        assert sent[0][cache.STABLE] == len(frame.stable_prompt(RULES))
        assert "Q-2041" in sent[0]["content"]        # the summary, framed
        assert sent[-2][cache.THROUGH] is True
        assert "Keep going" in sent[-1]["content"]

    def test_a_sub_assistant_is_asked_the_same_way(self):
        session, _ = build([
            action(action="spawn", goal="Save a note titled Ship.",
                   agents=["notebook"]),
            action(action="finish"),
            action(action="say", text="Saved."),          # child
            action(action="finish", summary="Saved it."),
            action(action="say", text="Done."),           # parent
            action(action="finish"),
        ], guardrail=RULES)

        async def scenario():
            await session.open()
            await session.deliver_user("get a note saved")
            await session.wait_idle()

        run(scenario())
        assert child_of(session).status == "done"
        calls = session.connector.calls
        assert len(calls) >= 6
        for call in calls:
            sent = call["messages"]
            assert sent[0][cache.STABLE] == len(frame.stable_prompt(RULES))
            assert sent[-1]["content"].startswith("PLAN\n")
            assert sent[-2][cache.THROUGH] is True


# ----------------------------------------------------------------------
# Anthropic, and Claude on Vertex
# ----------------------------------------------------------------------

class TestAnthropic:
    @staticmethod
    def connector(cls=AnthropicConnector):
        connector = AnthropicConnector({"api_key": "k", "model": "claude-x"})
        connector.__class__ = cls
        captured = {}

        async def create(**kwargs):
            captured.update(json.loads(json.dumps(kwargs)))
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")],
                                   stop_reason="end_turn", usage=None)

        connector.client = SimpleNamespace(messages=SimpleNamespace(create=create))
        return connector, captured

    def test_the_stable_front_the_tools_and_the_transcript_are_marked(self):
        connector, sent = self.connector()
        run(connector.chat(request(*HISTORY), tools=TOOLS))
        ephemeral = {"type": "ephemeral"}
        assert sent["system"] == [
            {"type": "text", "text": STABLE, "cache_control": ephemeral},
            {"type": "text", "text": REST}]
        assert sent["tools"][-1]["cache_control"] == ephemeral
        assert all("cache_control" not in t for t in sent["tools"][:-1])
        *before, last = sent["messages"]
        # The plan is after the last mark, unmarked, in the same turn of
        # the user's: the turns still alternate.
        assert last == {"role": "user", "content": [
            {"type": "text", "text": HISTORY[-1]["content"],
             "cache_control": ephemeral},
            {"type": "text", "text": "PLAN\n(no plan yet)"}]}
        assert all(marks(m) == 0 for m in before)
        assert alternating(sent["messages"])
        assert marks(sent) == 3
        no_hint_in(sent)

    def test_after_the_models_own_turn_the_plan_is_a_turn_of_its_own(self):
        connector, sent = self.connector()
        run(connector.chat(request(*HISTORY[:2])))
        assert sent["messages"][-2]["role"] == "assistant"
        assert marks(sent["messages"][-2]) == 1
        assert sent["messages"][-1] == {"role": "user",
                                        "content": "PLAN\n(no plan yet)"}

    def test_a_picture_keeps_its_blocks_and_the_mark_goes_on_the_last(self):
        connector, sent = self.connector()
        picture = AnthropicConnector.image_block("image/png", "QUJD")
        blocks = [{"type": "text", "text": "what is this?"}, picture]
        run(connector.chat(request({"role": "user", "content": blocks})))
        content = sent["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "what is this?"}
        assert content[1]["source"] == picture["source"]
        assert content[1]["cache_control"] == {"type": "ephemeral"}
        assert content[2] == {"type": "text", "text": "PLAN\n(no plan yet)"}
        assert "cache_control" not in picture             # the caller's untouched

    def test_never_more_than_four_marks(self):
        connector, sent = self.connector()
        many = [{"role": "user" if n % 2 else "assistant", "content": f"line {n}",
                 cache.THROUGH: True} for n in range(1, 9)]
        run(connector.chat(request(*many), tools=TOOLS))
        assert marks(sent) == cache.MAX_BREAKPOINTS
        # The latest are the ones kept.
        assert marks(sent["messages"][-2]) == 1
        no_hint_in(sent)

    def test_a_caller_that_says_nothing_is_asked_as_before(self):
        connector, sent = self.connector()
        run(connector.chat([{"role": "system", "content": "sys"},
                            {"role": "user", "content": "q"}], tools=TOOLS))
        assert sent["system"] == "sys"
        assert sent["messages"] == [{"role": "user", "content": "q"}]
        assert marks(sent) == 0

    def test_claude_on_vertex_is_marked_the_same(self):
        assert issubclass(VertexAnthropicConnector, AnthropicConnector)
        assert VertexAnthropicConnector.chat is AnthropicConnector.chat
        connector, sent = self.connector(VertexAnthropicConnector)
        run(connector.chat(request(*HISTORY), tools=TOOLS))
        assert marks(sent) == 3
        no_hint_in(sent)


# ----------------------------------------------------------------------
# Bedrock
# ----------------------------------------------------------------------

class TestBedrock:
    ENDPOINT = "https://bedrock-runtime.eu-west-1.amazonaws.com"
    POINT = {"cachePoint": {"type": "default"}}

    def connector(self, model, answers):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return answers.pop(0) if len(answers) > 1 else answers[0]

        connector = LLMConnectorFactory.create({
            "provider": "amazon-bedrock", "endpoint": self.ENDPOINT,
            "api_key": "bedrock-api-key-test", "model": model, "max_retries": 0})
        connector._client = lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler))
        return connector, seen

    @staticmethod
    def ok():
        return httpx.Response(200, json={
            "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
            "stopReason": "end_turn"})

    def points(self, value) -> int:
        return json.dumps(value).count('"cachePoint"')

    def test_claude_is_sent_cache_points_in_system_tools_and_messages(self):
        connector, seen = self.connector(
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0", [self.ok()])
        run(connector.chat(request(*HISTORY), tools=TOOLS))
        body = seen[0]
        assert body["system"] == [{"text": STABLE}, self.POINT, {"text": REST}]
        assert body["toolConfig"]["tools"][-1] == self.POINT
        last = body["messages"][-1]
        # The observation and the plan share the user's turn — Converse
        # takes no two of one role in a row — with the point between.
        assert last["role"] == "user"
        assert last["content"] == [{"text": HISTORY[-1]["content"]}, self.POINT,
                                   {"text": "PLAN\n(no plan yet)"}]
        assert self.points(body) == 3 <= cache.MAX_BREAKPOINTS
        no_hint_in(body)

    def test_never_more_than_four_points(self):
        connector, seen = self.connector(
            "anthropic.claude-sonnet-4-5-20250929-v1:0", [self.ok()])
        many = [{"role": "user" if n % 2 else "assistant", "content": f"line {n}",
                 cache.THROUGH: True} for n in range(1, 9)]
        run(connector.chat(request(*many), tools=TOOLS))
        assert self.points(seen[0]) == cache.MAX_BREAKPOINTS
        # The latest are the ones kept: the model's last line, then the
        # plan as the user's turn after it.
        assert seen[0]["messages"][-2:] == [
            {"role": "assistant", "content": [{"text": "line 8"}, self.POINT]},
            {"role": "user", "content": [{"text": "PLAN\n(no plan yet)"}]}]

    def test_nova_caches_no_tools(self):
        connector, seen = self.connector("amazon.nova-pro-v1:0", [self.ok()])
        run(connector.chat(request(*HISTORY), tools=TOOLS))
        assert self.POINT not in seen[0]["toolConfig"]["tools"]
        assert self.points(seen[0]) == 2

    @pytest.mark.parametrize("model", [
        "meta.llama3-70b-instruct-v1:0", "mistral.mistral-large-2407-v1:0",
        "arn:aws:bedrock:eu-west-1:123456789012:application-inference-profile/abc"])
    def test_a_model_not_known_to_cache_is_sent_none(self, model):
        connector, seen = self.connector(model, [self.ok()])
        run(connector.chat(request(*HISTORY), tools=TOOLS))
        assert self.points(seen[0]) == 0
        no_hint_in(seen[0])

    def test_a_refused_cache_point_is_asked_again_without_and_remembered(self):
        refused = httpx.Response(400, json={
            "message": "The model does not support prompt caching (cachePoint)."})
        connector, seen = self.connector(
            "anthropic.claude-3-haiku-20240307-v1:0", [refused, self.ok()])
        reply = run(connector.chat(request(*HISTORY), tools=TOOLS))
        assert reply.content == "ok"
        assert [self.points(body) for body in seen] == [3, 0]
        run(connector.chat(request(*HISTORY), tools=TOOLS))
        assert self.points(seen[-1]) == 0          # not tried again
        assert connector.cache_points is False

    def test_any_other_refusal_is_the_refusal(self):
        refused = httpx.Response(400, json={"message": "Malformed input request"})
        connector, seen = self.connector(
            "anthropic.claude-sonnet-4-5-20250929-v1:0", [refused])
        with pytest.raises(Exception, match="Malformed input"):
            run(connector.chat(request(*HISTORY), tools=TOOLS))
        assert len(seen) == 1 and connector.cache_points is True

    def test_a_caller_that_says_nothing_is_asked_as_before(self):
        connector, seen = self.connector(
            "anthropic.claude-sonnet-4-5-20250929-v1:0", [self.ok()])
        run(connector.chat([{"role": "system", "content": "sys"},
                            {"role": "user", "content": "q"}], tools=TOOLS))
        assert self.points(seen[0]) == 0
        assert seen[0]["system"] == [{"text": "sys"}]


# ----------------------------------------------------------------------
# OpenAI: chat completions and Responses
# ----------------------------------------------------------------------

class TestOpenAI:
    @staticmethod
    def chat_connector(**config):
        connector = OpenAIConnector({"api_key": "k", "model": "gpt-x", **config})
        seen = {}

        async def create(**kwargs):
            seen.update(kwargs)
            message = SimpleNamespace(content="ok", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=message, finish_reason="stop")], usage=None)

        connector.client = SimpleNamespace(
            base_url=connector.client.base_url,
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return connector, seen

    def test_openais_own_api_is_told_the_chat_by_a_hash(self):
        connector, seen = self.chat_connector()
        connector.cache_for("chat_42")
        run(connector.chat(request(*HISTORY)))
        key = seen["prompt_cache_key"]
        assert key == connector.cache_key and len(key) == 16
        assert "chat_42" not in key
        no_hint_in(seen["messages"])
        # The frame goes as one system message, as it always has, and
        # the plan in the person's last turn: user, assistant, user.
        assert seen["messages"][0] == {"role": "system",
                                       "content": f"{STABLE}\n\n{REST}"}
        assert seen["messages"][-1] == {
            "role": "user",
            "content": f"{HISTORY[-1]['content']}\n\nPLAN\n(no plan yet)"}
        assert alternating(seen["messages"])

    @pytest.mark.parametrize("endpoint", [
        "http://localhost:11434/v1", "https://openrouter.ai/api/v1",
        "https://gw.example.com/openai/v1"])
    def test_a_compatible_server_is_sent_neither_the_key_nor_a_hint(self, endpoint):
        connector, seen = self.chat_connector(endpoint=endpoint)
        connector.cache_for("chat_42")
        run(connector.chat(request(*HISTORY), tools=TOOLS))
        assert "prompt_cache_key" not in seen
        no_hint_in(seen)
        assert all(set(m) <= {"role", "content"} for m in seen["messages"])

    def test_a_connector_built_for_no_chat_sends_no_key(self):
        connector, seen = self.chat_connector()
        run(connector.chat(request(*HISTORY)))
        assert "prompt_cache_key" not in seen

    @staticmethod
    def responses_connector(base_url):
        from openai import AsyncOpenAI
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={
                "id": "resp_1", "object": "response", "created_at": 0,
                "model": "gpt-5", "status": "completed", "parallel_tool_calls": True,
                "tool_choice": "auto", "tools": [], "output": [{
                    "id": "msg_1", "type": "message", "role": "assistant",
                    "status": "completed", "content": [
                        {"type": "output_text", "text": "ok", "annotations": []}]}]})

        connector = OpenAIResponsesConnector({"api_key": "k", "model": "gpt-5"})
        connector.client = AsyncOpenAI(
            api_key="k", base_url=base_url, max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        connector.cache_for("chat_42")
        return connector, seen

    def test_responses_on_openai_carries_the_key_and_no_hint(self):
        connector, seen = self.responses_connector("https://api.openai.com/v1")
        run(connector.chat(request(*HISTORY), tools=TOOLS))
        assert seen[0]["prompt_cache_key"] == connector.cache_key
        assert seen[0]["instructions"] == f"{STABLE}\n\n{REST}"
        assert seen[0]["input"][-1]["content"].endswith("PLAN\n(no plan yet)")
        assert alternating(seen[0]["input"])
        no_hint_in(seen[0])

    def test_responses_through_a_gateway_carries_neither(self):
        connector, seen = self.responses_connector("https://gw.example.com/v1")
        run(connector.chat(request(*HISTORY), tools=TOOLS))
        assert "prompt_cache_key" not in seen[0]
        no_hint_in(seen[0])

    def test_the_host_names_the_chat_to_the_connector_it_builds(self):
        connector = SessionHost._connector(
            {"provider": "openai", "api_key": "k", "model": "gpt-5"}, "", "chat_42")
        assert connector.cache_key and "chat_42" not in connector.cache_key
        other = SessionHost._connector(
            {"provider": "openai", "api_key": "k", "model": "gpt-5"}, "", "chat_43")
        assert other.cache_key != connector.cache_key
        # One that takes no key is built as it was.
        scripted = SessionHost._connector({"provider": "fake"}, "", "chat_42")
        assert isinstance(scripted, FakeConnector)


# ----------------------------------------------------------------------
# Gemini, and Gemini on Vertex
# ----------------------------------------------------------------------

class TestGemini:
    def test_no_hint_is_sent_and_the_front_stays_put(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"candidates": [{
                "content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]})

        connector = GeminiConnector({
            "api_key": "k", "model": "gemini-x",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta"})
        connector._client = lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler))
        run(connector.chat(request(*HISTORY), tools=TOOLS))
        run(connector.chat(request(*HISTORY, plan="PLAN\nw1 [done] x"), tools=TOOLS))
        first, second = seen
        no_hint_in(first)
        # Everything before the plan is the same from one beat to the next.
        assert first["systemInstruction"] == second["systemInstruction"]
        assert first["tools"] == second["tools"]
        assert first["contents"][:-1] == second["contents"][:-1]
        assert first["contents"][-1]["parts"][:-1] == second["contents"][-1]["parts"][:-1]


# ----------------------------------------------------------------------
# Pictures
# ----------------------------------------------------------------------

class TestPictures:
    def test_the_same_stored_picture_is_the_same_bytes_every_call(self):
        reads = []

        async def read_image(ref):
            reads.append(ref)
            return {"content_base64": "iVBORw0KGgo=", "file_type": "image/png"}

        harness = Harness([action(action="finish")], read_image=read_image)
        harness.assistant.connector = AnthropicConnector(
            {"api_key": "k", "model": "claude-x"})
        harness.state.messages = [
            {"role": "system", "content": "frame"},
            {"role": "user", "content": "look",
             "images": [{"resource_ref": "fil_1", "file_type": "image/png"}]}]
        first = run(harness.assistant._request())
        second = run(harness.assistant._request())
        assert json.dumps(first) == json.dumps(second)
        assert len(reads) == 2
