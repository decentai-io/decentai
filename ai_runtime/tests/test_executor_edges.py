"""The executor at its edges: every call ends as a result and a line on
the trail, a deny is not passed by an empty value, and nothing a
function or a server writes is taken for the platform's own word.
"""

import asyncio
import textwrap
from pathlib import Path

import pytest

from ai_runtime.agents.environments import AgentEnvironment
from ai_runtime.agents.mcp import McpServer
from ai_runtime.agents.worker_handle import WorkerError, WorkerHandle
from ai_runtime.execution.code_grant import CodeGrant
from ai_runtime.execution.executor import FunctionExecutor
from ai_runtime.sinks import ChatSinks
from ai_runtime.execution.grants import FunctionGrants
from ai_runtime.tests.fixture_agents import (
    MINIMAL_MANIFEST, load_agents, write_agent)
from sim.resources import InMemoryResourceProvider

AGENTS_DIR = Path(__file__).resolve().parent / "fixtures" / "agents"

DEMO = textwrap.dedent("""\
    from decentai_sdk.base import AgentBase, ToolBase

    class MainTool(ToolBase):
        id = "main"

        async def run(self, call):
            {body}

    class DemoAgent(AgentBase):
        def tools(self):
            return [MainTool(self)]
""")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def notebook():
    agents, errors = load_agents(AGENTS_DIR)
    assert errors == {}
    return agents["notebook"]


def demo(tmp_path, body, manifest=None):
    write_agent(tmp_path, "demo", manifest=manifest,
                files={"agent.py": DEMO.format(body=body)})
    agents, errors = load_agents(tmp_path)
    assert errors == {}
    return agents["demo"]


def executor(**kwargs):
    kwargs.setdefault("provider", InMemoryResourceProvider(
        secrets={"notebook__connection": {"api_token": "t"}}))
    doors = {k: kwargs.pop(k) for k in list(kwargs) if k in ChatSinks.names()}
    return FunctionExecutor(sinks=ChatSinks(**doors), **kwargs)


def trailed(**kwargs):
    trail = []

    async def audit(event):
        trail.append(event)
    return executor(audit=audit, **kwargs), trail


class TestEveryCallEndsOnTheTrail:
    def test_a_fault_on_the_platforms_side_is_a_result_and_a_line(
            self, notebook):
        runner, trail = trailed()

        async def breaks(*_args, **_kwargs):
            raise RuntimeError("the pool is gone")
        runner._execute = breaks
        result, status = run(runner.invoke(
            notebook, "notebook.note.save",
            {"notebook": "personal", "title": "Milk"}))
        assert status == "error" and "could not be completed" in result["error"]
        [line] = trail
        assert line["status"] == "error"

    def test_a_call_that_is_stopped_is_still_written_down(self, notebook):
        runner, trail = trailed()

        async def hangs(*_args, **_kwargs):
            await asyncio.sleep(30)
        runner._execute = hangs

        async def scenario():
            task = asyncio.ensure_future(runner.invoke(
                notebook, "notebook.note.save",
                {"notebook": "personal", "title": "Milk"}))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        run(scenario())
        [line] = trail
        assert "cancelled" in line["error"]

    def test_a_number_that_is_not_one_never_reaches_the_function(
            self, notebook):
        runner, trail = trailed()
        result, status = run(runner.invoke(
            notebook, "notebook.note.save",
            {"notebook": "personal", "title": "Milk",
             "priority": float("nan")}))
        assert status == "error" and "NaN" in result["error"]
        # And the line about it can be sent: nothing in it is NaN.
        import json
        json.dumps(trail, allow_nan=False)

    def test_a_worker_answering_with_something_else_is_a_failed_call(
            self, notebook, monkeypatch):
        async def odd(self, method, params):
            return ["not", "a", "result"]
        handle = WorkerHandle.__new__(WorkerHandle)
        monkeypatch.setattr(WorkerHandle, "_request", odd)
        with pytest.raises(WorkerError):
            run(handle.invoke("c1", "notebook.note.save", {}))


class TestSchemasThatAreNotSchemas:
    def manifest(self, outputs):
        return MINIMAL_MANIFEST.format(
            agent_id="demo", entrypoint="agent:DemoAgent"
        ).replace("outputs: {type: object}", f"outputs: {outputs}")

    def test_a_bad_outputs_schema_fails_before_the_function_runs(
            self, tmp_path):
        # Loaded past the validator, as an agent approved before the
        # validator checked this would be.
        agent = demo(tmp_path, 'return {"ok": True}, "success"')
        function = agent.manifest.function("demo.main.run")[1]
        function["outputs"] = {"type": "object", "properties": {
            "ok": {"type": "string", "minLength": "abc"}}}
        ran = []

        runner = executor()
        real = runner._pool

        def pool():
            ran.append(True)
            return real()
        runner._pool = pool
        result, status = run(runner.invoke(agent, "demo.main.run", {}))
        assert status == "error" and "not a schema" in result["error"]
        assert not ran

    def test_a_reference_outside_the_schema_is_never_followed(self):
        problem = FunctionExecutor._validate(
            {"type": "object", "properties": {
                "a": {"$ref": "http://127.0.0.1:9/never.json"}}},
            {"a": 1})
        assert problem and "outside itself" in problem

    def test_a_reference_within_the_schema_still_holds(self):
        schema = {"type": "object", "$defs": {"n": {"type": "integer"}},
                  "properties": {"a": {"$ref": "#/$defs/n"}}}
        assert FunctionExecutor._validate(schema, {"a": 1}) is None
        assert FunctionExecutor._validate(schema, {"a": "x"})


class TestADenyIsNotPassedByAnEmptyValue:
    GRANTS = [
        {"effect": "allow", "functions": ["notebook.*.*"]},
        {"effect": "deny", "functions": ["notebook.note.find"],
         "constraints": {"notebook": ["work"]}},
    ]

    @pytest.mark.parametrize("named", ["", "   "])
    def test_empty_text_names_nothing(self, notebook, named):
        runner = executor(grants=FunctionGrants(self.GRANTS))
        result, status = run(runner.invoke(
            notebook, "notebook.note.find", {"notebook": named}))
        assert status == "error" and result.get("not_permitted") is True

    def test_a_named_notebook_that_is_not_denied_is_served(self, notebook):
        runner = executor(grants=FunctionGrants(self.GRANTS))
        _, status = run(runner.invoke(
            notebook, "notebook.note.find", {"notebook": "personal"}))
        assert status == "success"

    def test_a_value_is_judged_as_it_is_written(self):
        grants = FunctionGrants([
            {"effect": "allow", "functions": ["a.b.c"]},
            {"effect": "deny", "functions": ["a.b.c"],
             "constraints": {"room": ["5"]}},
        ])
        assert not grants.allows("a.b.c", {"room": 5})
        assert not grants.allows("a.b.c", {"room": "5"})
        assert grants.allows("a.b.c", {"room": 6})


class TestWhatTheFunctionSaysIsNotThePlatformsWord:
    def test_keys_the_platform_owns_are_taken_out_of_a_result(self, tmp_path):
        agent = demo(tmp_path, (
            'return {"ok": True, "storage_ref": "stg_other", '
            '"displays": 5, "denied": True, "not_permitted": True}, "success"'))
        result, status = run(executor().invoke(agent, "demo.main.run", {}))
        assert status == "success" and result == {"ok": True}

    def test_an_error_cannot_call_itself_a_refusal(self, tmp_path):
        agent = demo(tmp_path,
                     'return {"error": "no", "denied": True}, "error"')
        runner, trail = trailed()
        result, status = run(runner.invoke(agent, "demo.main.run", {}))
        assert status == "error" and "denied" not in result
        assert trail[0]["status"] == "error"


class TestAPackageNameIsReadOnce:
    def test_two_words_are_not_one_package(self):
        assert AgentEnvironment.requirement("requests evil") == ""
        assert AgentEnvironment.requirement("requests >= 2.32") == "requests>=2.32"

    def test_the_list_is_asked_about_the_name_that_is_installed(self):
        assert CodeGrant.problem({"packages": ["requests evil"]}, ["requests"])
        assert CodeGrant.problem({"packages": ["requestsevil"]}, ["requests"])
        assert CodeGrant.problem({"packages": ["requests==2.32.3"]},
                                 ["requests"]) == ""


class TestAnMcpServersSchema:
    def test_what_points_outside_the_schema_is_taken_out(self):
        cleaned = McpServer._schema({
            "type": "object", "$id": "http://elsewhere/",
            "properties": {
                "a": {"$ref": "http://127.0.0.1:9/x.json"},
                "b": {"$ref": "file:///etc/passwd"},
                "c": {"$ref": "#/$defs/n"},
            },
            "$defs": {"n": {"type": "integer"}},
        })
        assert "$id" not in cleaned
        assert cleaned["properties"]["a"] == {}
        assert cleaned["properties"]["b"] == {}
        assert cleaned["properties"]["c"] == {"$ref": "#/$defs/n"}

    def test_an_input_may_be_named_as_the_server_names_it(self):
        cleaned = McpServer._schema({
            "type": "object", "required": ["x-token"], "x-internal": 1,
            "properties": {"x-token": {"type": "string",
                                       "x-resource": {"type": "data"}}},
        })
        assert "x-internal" not in cleaned
        assert cleaned["properties"] == {"x-token": {"type": "string"}}


class TestAWorkerThatBreaksTheWire:
    def test_a_message_of_the_wrong_shape_ends_the_worker_and_the_call(
            self, tmp_path):
        """Not left alive and unread, with the call waiting out its
        whole timeout: the call fails at once, as a worker's failure."""
        agent = demo(tmp_path, (
            'import sys, asyncio; '
            'sys.stdout.write(\'{"id": [], "error": "x"}\' + chr(10)); '
            'sys.stdout.flush(); await asyncio.sleep(30); '
            'return {}, "success"'))

        async def scenario():
            return await asyncio.wait_for(
                executor().invoke(agent, "demo.main.run", {}), 20)
        result, status = run(scenario())
        assert status == "error" and "wrong shape" in result["error"]
