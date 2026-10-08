"""`llm: true` functions — the chat's model as a mediated capability.

The manifest declares which functions think with the deployment's model;
the executor injects ``call.llm`` only for those, backed by the chat's
connector — so the stored LLM key never reaches agent code, and an
undeclared function cannot reach the model even when the executor holds
one. Driven by the real LLM agent fixture plus stub agents.
"""

import asyncio
import textwrap
from pathlib import Path

import pytest

from contracts.agent_manifest import ManifestValidator
from ai_runtime.execution.executor import FunctionExecutor
from ai_runtime.sinks import ChatSinks
from ai_runtime.tests.fixture_agents import load_agents
from ai_runtime.llms import FakeConnector

from ai_runtime.tests.fixture_agents import MINIMAL_MANIFEST, write_agent
from decentai_sdk.base import Completion

AGENTS_DIR = Path(__file__).resolve().parent / "fixtures" / "agents"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def llm_agent():
    agents, errors = load_agents(AGENTS_DIR)
    assert errors == {}
    return agents["llm"]


def make_executor(responses, **kwargs):
    """An executor whose model is a scripted FakeConnector."""
    connector = FakeConnector(responses)

    async def llm(messages, max_tokens=None):
        # The session hands the executor the model's words and how it
        # stopped, as the SDK's Completion (chat/session.py `_llm`).
        reply = await connector.chat(messages, max_tokens)
        return Completion(reply.content, reply.stop_reason)

    executor = FunctionExecutor(sinks=ChatSinks(llm=llm), **kwargs)
    return executor, connector


PEEKING_MANIFEST = textwrap.dedent("""    schema_version: "1.0"
    agent:
      id: peeker
      name: Peeker
      version: "1.0.0"
      description: Shows the model a scan.
    network:
      hosts: []
    implementation:
      entrypoint: agent:DemoAgent
    resources:
      files:
        - id: scan
          label: Scan
          description: A picture the person handed over.
          binding: {cardinality: many, required: false}
    tools:
      - id: main
        name: Main
        description: The one tool.
        resources:
          files: [scan]
        functions:
          - id: read
            name: Read
            description: Read a scan with the model.
            permission_level: 0
            llm: true
            resources:
              files: {scan: [read]}
            inputs: {type: object}
            outputs: {type: object}
""")

PEEKING_AGENT = textwrap.dedent("""    from decentai_sdk.base import AgentBase, ResourceDenied, ToolBase

    class MainTool(ToolBase):
        id = "main"

        async def read(self, call):
            seen = await call.llm("What is the expiry date?",
                                  images=[{"resource_id": "scan", "ref": call.inputs["ref"]}])
            try:
                await call.llm("And this?", images=[{"resource_id": "scan", "ref": "fil_text"}])
                text_too = "accepted"
            except ResourceDenied as exc:
                text_too = str(exc)
            return {"seen": seen, "text_too": text_too}, "success"

    class DemoAgent(AgentBase):
        def tools(self):
            return [MainTool(self)]
""")


#: How a PNG file begins.
PNG = bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A])


class TestPictures:
    def test_what_is_called_a_picture_must_be_one(self):
        import base64
        from ai_runtime.execution.pictures import Pictures

        def encoded(raw):
            return base64.b64encode(raw).decode("ascii")

        assert Pictures.problem("image/png", encoded(PNG + b"pixels")) == ""
        assert Pictures.problem("image/jpeg", encoded(bytes([0xFF, 0xD8, 0xFF, 0xE0]))) == ""
        assert "not image/jpeg" in Pictures.problem("image/jpeg", encoded(PNG))
        assert "not a picture" in Pictures.problem("text/html", encoded(b"<html>"))
        assert "not base64" in Pictures.problem("image/png", "%%%not base64%%%")
        assert "no content" in Pictures.problem("image/png", "")
        assert "larger than" in Pictures.problem(
            "image/png", encoded(PNG) + "A" * (8 * 1024 * 1024))
        # A screen's frame is a JPEG or a PNG and nothing else.
        assert "not a picture" in Pictures.problem(
            "image/gif", encoded(b"GIF89a"), Pictures.FRAME_TYPES)

    def test_an_agent_shows_the_model_a_scan_it_may_read(self, tmp_path):
        """call.llm(images=[…]) names a file; the executor reads it under
        the function's own file grant and hands the model the bytes —
        so an agent can have a passport scan read without ever holding
        the model's key, and a file that is not a picture is refused."""
        from sim.resources import InMemoryResourceProvider

        write_agent(tmp_path, "peeker", manifest=PEEKING_MANIFEST,
                    files={"agent.py": PEEKING_AGENT})
        agents, errors = load_agents(tmp_path)
        assert errors == {}
        provider = InMemoryResourceProvider()
        provider.files["peeker__scan"] = {
            "fil_scan": {"resource_ref": "fil_scan", "resource_id": "peeker__scan",
                         "filename": "passport.png", "file_type": "image/png",
                         "content": PNG + b"fictional pixels"},
            # Called a picture, and not one.
            "fil_fake": {"resource_ref": "fil_fake", "resource_id": "peeker__scan",
                         "filename": "page.png", "file_type": "image/png",
                         "content": b"<html>not pixels</html>"},
            "fil_text": {"resource_ref": "fil_text", "resource_id": "peeker__scan",
                         "filename": "notes.txt", "file_type": "text/plain",
                         "content": b"just words"},
        }
        shown = []

        async def llm(messages, max_tokens=None, images=None):
            shown.append((messages[-1]["content"], images))
            return "12 March 2031"

        executor = FunctionExecutor(provider=provider, sinks=ChatSinks(llm=llm))
        result, status = run(executor.invoke(
            agents["peeker"], "peeker.main.read", {"ref": "fil_scan"}))
        assert status == "success", result
        assert result["seen"] == "12 March 2031"
        assert "not an image" in result["text_too"]
        [(prompt, pictures)] = shown
        assert prompt == "What is the expiry date?"
        assert [p["mime"] for p in pictures] == ["image/png"]
        import base64
        assert base64.b64decode(pictures[0]["content_base64"]).startswith(PNG)

        # A file that says it is a picture and is not is refused too:
        # what a file is called is not what it is.
        result, status = run(executor.invoke(
            agents["peeker"], "peeker.main.read", {"ref": "fil_fake"}))
        assert status == "error" and "not image/png" in str(result)
        assert len(shown) == 1


class TestAScreensFrame:
    """A frame is small because many are sent, to every browser that
    watches. The SDK keeps to the size; the platform holds an agent
    that does not use it to the same."""

    @staticmethod
    def shown(frame_bytes):
        import base64
        from types import SimpleNamespace

        sent = []

        async def sink(kind, frame, source):
            sent.append(kind)

        executor = FunctionExecutor(sinks=ChatSinks(screen=sink))
        agent = SimpleNamespace(
            agent_id="agt_probe", manifest=SimpleNamespace(name="Probe"))
        screen = executor.surface.screen_for(agent, "probe.main.watch", "c_1")
        run(screen("frame", {
            "image_base64": base64.b64encode(PNG + b"x" * frame_bytes).decode(),
            "mime": "image/png", "width": 800, "height": 600, "frame": 1}))
        return sent

    def test_one_within_the_size_is_shown(self):
        assert self.shown(200_000) == ["frame"]

    def test_one_above_it_is_dropped_and_the_function_runs_on(self):
        from contracts.chat import SCREEN_FRAME_MAX_BYTES

        assert self.shown(SCREEN_FRAME_MAX_BYTES + 1) == []


class TestAScreenEndsWithItsCall:
    """A screen is a call's. One the function showed and never closed
    is closed for it when the call is over, so that a picture nobody
    is behind does not stand in the chat as though something ran."""

    SHOWS = textwrap.dedent("""\
        from decentai_sdk.base import AgentBase, ToolBase

        PNG = bytes.fromhex("89504e470d0a1a0a") + b"x" * 64

        class MainTool(ToolBase):
            id = "main"

            async def run(self, call):
                what = call.inputs.get("what")
                await call.screen.show(PNG, 800, 600, mime="image/png")
                if what == "closes":
                    await call.screen.close()
                if what == "raises":
                    raise RuntimeError("it broke")
                return {"shown": True}, "success"

        class DemoAgent(AgentBase):
            def tools(self):
                return [MainTool(self)]
    """)

    def told(self, tmp_path, what):
        sent = []

        async def sink(kind, frame, source):
            sent.append(kind)

        write_agent(tmp_path, "demo", files={"agent.py": self.SHOWS})
        agents, errors = load_agents(tmp_path)
        assert errors == {}, errors
        agent = agents["demo"]
        result, status = run(FunctionExecutor(sinks=ChatSinks(screen=sink)).invoke(
            agent, "demo.main.run", {"what": what}, chat_level=2))
        return sent, status

    def test_one_left_open_is_closed_when_the_function_returns(self, tmp_path):
        assert self.told(tmp_path, "leaves") == (["frame", "closed"], "success")

    def test_one_left_open_is_closed_when_the_function_fails(self, tmp_path):
        assert self.told(tmp_path, "raises") == (["frame", "closed"], "error")

    def test_one_the_function_closed_is_not_closed_twice(self, tmp_path):
        assert self.told(tmp_path, "closes") == (["frame", "closed"], "success")


class TestTheLineBothEndsRead:
    def test_the_worker_reads_as_long_a_line_as_the_runtime_does(self):
        """Shorter on the worker's side, a line the runtime was right
        to send ended the worker that read it."""
        from ai_runtime.agents.spawner_service import SpawnerService
        from ai_runtime.agents.worker_handle import WorkerHandle
        from decentai_sdk import worker

        assert worker.MAX_LINE_BYTES == WorkerHandle.LINE_LIMIT \
            == SpawnerService.LINE_LIMIT


class TestManifestDeclaration:
    def _document(self, llm_line=""):
        import yaml

        text = MINIMAL_MANIFEST.format(
            agent_id="demo", entrypoint="agent:DemoAgent"
        )
        document = yaml.safe_load(text)
        function = document["tools"][0]["functions"][0]
        if llm_line != "":
            function["llm"] = llm_line
        return document

    def test_llm_true_is_valid(self):
        assert ManifestValidator().validate(self._document(True)) == []

    def test_llm_false_is_valid(self):
        assert ManifestValidator().validate(self._document(False)) == []

    def test_llm_must_be_boolean(self):
        errors = ManifestValidator().validate(self._document("yes please"))
        assert any("llm must be true or false" in error for error in errors)


class TestDeclaredFunctions:
    def test_summarize_reaches_the_model(self, llm_agent):
        executor, connector = make_executor(["A short summary."])
        result, status = run(executor.invoke(
            llm_agent, "llm.text.summarize",
            {"content": "Long page text here.", "max_words": 50},
        ))
        assert status == "success"
        assert result["summary"] == "A short summary."

        # The content travels as the user message — data, not directive —
        # and the task rides in the system prompt.
        [call] = connector.calls
        roles = [message["role"] for message in call["messages"]]
        assert roles == ["system", "user"]
        assert call["messages"][1]["content"] == "Long page text here."
        assert "50 words" in call["messages"][0]["content"]

    def test_extract_parses_the_model_json(self, llm_agent):
        executor, _ = make_executor(['{"vendor": "Acme", "total": 12.5}'])
        result, status = run(executor.invoke(
            llm_agent, "llm.text.extract",
            {"content": "Invoice from Acme, total 12.50",
             "instructions": "vendor and total"},
        ))
        assert status == "success"
        assert result["data"] == {"vendor": "Acme", "total": 12.5}

    def test_extract_unwraps_a_fenced_answer(self, llm_agent):
        executor, _ = make_executor(['```json\n{"vendor": "Acme"}\n```'])
        result, status = run(executor.invoke(
            llm_agent, "llm.text.extract",
            {"content": "…", "instructions": "vendor"},
        ))
        assert status == "success"
        assert result["data"] == {"vendor": "Acme"}

    def test_extract_reports_a_non_json_answer(self, llm_agent):
        executor, _ = make_executor(["Sorry, I cannot."])
        result, status = run(executor.invoke(
            llm_agent, "llm.text.extract",
            {"content": "…", "instructions": "vendor"},
        ))
        assert status == "error"
        assert "did not return JSON" in result["error"]

    def test_extract_wraps_a_non_object(self, llm_agent):
        executor, _ = make_executor(['["a", "b"]'])
        result, status = run(executor.invoke(
            llm_agent, "llm.text.extract",
            {"content": "…", "instructions": "the list"},
        ))
        assert status == "success"
        assert result["data"] == {"value": ["a", "b"]}

    def test_rewrite_returns_the_model_text(self, llm_agent):
        executor, _ = make_executor(["Bonjour."])
        result, status = run(executor.invoke(
            llm_agent, "llm.text.rewrite",
            {"content": "Hello.", "instructions": "in French"},
        ))
        assert status == "success"
        assert result["text"] == "Bonjour."


class TestTheBoundary:
    def test_without_a_model_the_reason_travels(self, llm_agent):
        # The scheduled-action path constructs the executor with no model;
        # the refusal must say what is missing, not just "failed".
        executor = FunctionExecutor()
        result, status = run(executor.invoke(
            llm_agent, "llm.text.summarize", {"content": "text"},
        ))
        assert status == "error"
        assert "llm: true" in result["error"]

    def test_an_undeclared_function_cannot_reach_the_model(self, tmp_path):
        # The manifest's `run` does not declare llm — the model must be
        # out of reach even though this executor holds one.
        write_agent(tmp_path, "demo", files={"agent.py": textwrap.dedent("""\
            from decentai_sdk.base import AgentBase, ToolBase

            class MainTool(ToolBase):
                id = "main"

                async def run(self, call):
                    return {"answer": await call.llm("hello")}, "success"

            class DemoAgent(AgentBase):
                def tools(self):
                    return [MainTool(self)]
        """)})
        agents, errors = load_agents(tmp_path)
        assert errors == {}

        executor, connector = make_executor(["never used"])
        result, status = run(executor.invoke(agents["demo"], "demo.main.run", {}))
        assert status == "error"
        assert "llm: true" in result["error"]
        assert connector.calls == []


class TestACutReply:
    def test_the_function_hears_that_the_reply_was_cut(self, tmp_path):
        """A provider that stops the model at a cap says so; the word
        travels the wire, and agent code reads it as ``answer.cut``
        instead of parsing half an answer."""
        manifest = MINIMAL_MANIFEST.format(agent_id="demo", entrypoint="agent:DemoAgent")
        write_agent(tmp_path, "demo", manifest=manifest.replace(
            "permission_level: 0", "permission_level: 0\n        llm: true"),
            files={"agent.py": textwrap.dedent("""\
            from decentai_sdk.base import AgentBase, ToolBase

            class MainTool(ToolBase):
                id = "main"

                async def run(self, call):
                    answer = await call.llm("go on")
                    return {"text": str(answer), "cut": answer.cut,
                            "stop_reason": answer.stop_reason}, "success"

            class DemoAgent(AgentBase):
                def tools(self):
                    return [MainTool(self)]
        """)})
        agents, errors = load_agents(tmp_path)
        assert errors == {}

        async def llm(messages, max_tokens=None):
            return Completion('{"action": {"do": "rem', "length")

        executor = FunctionExecutor(sinks=ChatSinks(llm=llm))
        result, status = run(executor.invoke(agents["demo"], "demo.main.run", {}))
        assert status == "success", result
        assert result == {"text": '{"action": {"do": "rem', "cut": True, "stop_reason": "length"}

